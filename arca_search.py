"""GestyTab - ricerca testo nelle note dei documenti (fatture) di Arca Evolution.

Si collega in SOLA LETTURA al database SQL Server di Arca Evolution e cerca una
parola/stringa nelle colonne note di testata (DOTes) e di riga (DORig),
filtrando per clienti, fornitori o entrambi.
"""
import csv
import json
import os
import sys
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    import pyodbc
except ImportError:  # messaggio chiaro invece di un crash
    pyodbc = None

APP_NAME = "GestyTab - Ricerca note Arca"
BASE_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "gestytab.json")

# --- Schema Arca Evolution (modificabile qui se la tua versione differisce) ---
T_TESTATA, T_RIGHE, T_ANAG = "DOTes", "DORig", "CF"
COL_ID, COL_TIPODOC, COL_NUM, COL_DATA, COL_CF = "Id_DOTes", "Cd_DO", "NumeroDoc", "DataDoc", "Cd_CF"
COL_ANAG_DESCR, COL_ANAG_CLI, COL_ANAG_FOR = "Descrizione", "Cliente", "Fornitore"
TEXT_TYPES = ("char", "varchar", "nchar", "nvarchar", "text", "ntext")
NOTE_HINTS = ("nota", "note")  # colonne selezionate di default

# --- stile (richiama la pagina "Informazioni su" di Windows 10) ---
FONT = "Segoe UI"
WHITE, BAR, BORDER, GREY, LINK = "#ffffff", "#f2f2f2", "#e5e5e5", "#707070", "#0063b1"
BTN, BTN_HOVER = "#cccccc", "#b8b8b8"

DEFAULT_CONFIG = {
    "server": "localhost\\SQLEXPRESS", "database": "", "windows_auth": True,
    "user": "", "columns": [], "ignore_accents": True,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


def pick_driver():
    prefs = ["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server",
             "ODBC Driver 13 for SQL Server", "SQL Server Native Client 11.0", "SQL Server"]
    installed = pyodbc.drivers()
    for d in prefs:
        if d in installed:
            return d
    raise RuntimeError("Nessun driver ODBC per SQL Server trovato. Installa "
                       "'ODBC Driver 17/18 for SQL Server' da Microsoft.")


def connect(cfg, password):
    parts = [f"DRIVER={{{pick_driver()}}}", f"SERVER={cfg['server']}", f"DATABASE={cfg['database']}",
             "ApplicationIntent=ReadOnly", "TrustServerCertificate=yes", "Encrypt=no"]
    if cfg["windows_auth"]:
        parts.append("Trusted_Connection=yes")
    else:
        parts += [f"UID={cfg['user']}", f"PWD={password}"]
    return pyodbc.connect(";".join(parts), timeout=10, autocommit=True)


def list_databases(cfg, password):
    c = dict(cfg, database="master")
    with connect(c, password) as cn:
        rows = cn.cursor().execute(
            "SELECT name FROM sys.databases WHERE database_id > 4 ORDER BY name").fetchall()
    return [r[0] for r in rows]


def discover_columns(cn):
    """Colonne testo di testata e righe: [(tabella, colonna)]."""
    cur = cn.cursor()
    found = []
    for table in (T_TESTATA, T_RIGHE):
        rows = cur.execute(
            "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = ? ORDER BY ORDINAL_POSITION", table).fetchall()
        found += [(table, n) for n, t in rows if t in TEXT_TYPES]
    return found


def default_selection(cols):
    return [f"{t}.{c}" for t, c in cols if any(h in c.lower() for h in NOTE_HINTS)]


def esc_like(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("[", "\\[")


def build_query(table, col, scope, with_dates, ignore_accents):
    coll = " COLLATE Latin1_General_CI_AI" if ignore_accents else ""
    if table == T_TESTATA:
        frm = f"[{T_TESTATA}] t"
    else:
        frm = f"[{T_RIGHE}] r JOIN [{T_TESTATA}] t ON t.[{COL_ID}] = r.[{COL_ID}]"
    src = "r" if table == T_RIGHE else "t"
    where = [f"CAST({src}.[{col}] AS nvarchar(max)){coll} LIKE ? ESCAPE '\\'"]
    if scope == "clienti":
        where.append(f"a.[{COL_ANAG_CLI}] = 1")
    elif scope == "fornitori":
        where.append(f"a.[{COL_ANAG_FOR}] = 1")
    if with_dates:
        where.append(f"t.[{COL_DATA}] >= ? AND t.[{COL_DATA}] < DATEADD(day, 1, ?)")
    return (f"SELECT t.[{COL_TIPODOC}], t.[{COL_NUM}], t.[{COL_DATA}], t.[{COL_CF}], a.[{COL_ANAG_DESCR}], "
            f"CAST({src}.[{col}] AS nvarchar(max)) "
            f"FROM {frm} LEFT JOIN [{T_ANAG}] a ON a.[{COL_CF}] = t.[{COL_CF}] "
            f"WHERE " + " AND ".join(where) +
            f" ORDER BY t.[{COL_DATA}] DESC, t.[{COL_NUM}] DESC")


def snippet(text, term, width=70):
    text = " ".join((text or "").split())
    i = text.lower().find(term.lower())
    if i < 0 or len(text) <= width * 2:
        return text[: width * 2]
    a, b = max(0, i - width), min(len(text), i + len(term) + width)
    return ("…" if a else "") + text[a:b] + ("…" if b < len(text) else "")


class ConnectDialog(tk.Toplevel):
    def __init__(self, master, cfg):
        super().__init__(master)
        self.title("Connessione a Arca Evolution")
        self.resizable(False, False)
        self.cfg, self.password, self.ok = cfg, "", False
        self.v_server = tk.StringVar(value=cfg["server"])
        self.v_db = tk.StringVar(value=cfg["database"])
        self.v_win = tk.BooleanVar(value=cfg["windows_auth"])
        self.v_user = tk.StringVar(value=cfg["user"])
        self.v_pwd = tk.StringVar()
        f = ttk.Frame(self, padding=12)
        f.pack()
        ttk.Label(f, text="Server SQL:").grid(row=0, column=0, sticky="e", pady=3)
        ttk.Entry(f, textvariable=self.v_server, width=36).grid(row=0, column=1, columnspan=2)
        ttk.Label(f, text="Database:").grid(row=1, column=0, sticky="e", pady=3)
        self.cb_db = ttk.Combobox(f, textvariable=self.v_db, width=33)
        self.cb_db.grid(row=1, column=1)
        ttk.Button(f, text="Elenca", width=7, command=self.fill_dbs).grid(row=1, column=2)
        ttk.Checkbutton(f, text="Autenticazione di Windows", variable=self.v_win,
                        command=self.toggle).grid(row=2, column=1, sticky="w")
        ttk.Label(f, text="Utente:").grid(row=3, column=0, sticky="e", pady=3)
        self.e_user = ttk.Entry(f, textvariable=self.v_user, width=36)
        self.e_user.grid(row=3, column=1, columnspan=2)
        ttk.Label(f, text="Password:").grid(row=4, column=0, sticky="e", pady=3)
        self.e_pwd = ttk.Entry(f, textvariable=self.v_pwd, show="*", width=36)
        self.e_pwd.grid(row=4, column=1, columnspan=2)
        ttk.Button(f, text="Connetti", command=self.go).grid(row=5, column=1, pady=(10, 0), sticky="e")
        ttk.Button(f, text="Annulla", command=self.destroy).grid(row=5, column=2, pady=(10, 0))
        self.toggle()
        self.bind("<Return>", lambda e: self.go())
        self.transient(master)
        self.grab_set()

    def toggle(self):
        st = "disabled" if self.v_win.get() else "normal"
        self.e_user.config(state=st)
        self.e_pwd.config(state=st)

    def collect(self):
        self.cfg.update(server=self.v_server.get().strip(), database=self.v_db.get().strip(),
                        windows_auth=self.v_win.get(), user=self.v_user.get().strip())
        return self.v_pwd.get()

    def fill_dbs(self):
        try:
            self.cb_db["values"] = list_databases(self.cfg, self.collect())
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Impossibile leggere i database:\n{e}", parent=self)

    def go(self):
        self.password = self.collect()
        if not self.cfg["database"]:
            messagebox.showwarning(APP_NAME, "Indica il database.", parent=self)
            return
        self.ok = True
        self.destroy()


class ColumnsDialog(tk.Toplevel):
    def __init__(self, master, cols, selected):
        super().__init__(master)
        self.title("Campi in cui cercare")
        self.result = None
        ttk.Label(self, text="Spunta i campi testo da includere nella ricerca:").pack(padx=10, pady=(10, 4), anchor="w")
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, padx=10)
        canvas = tk.Canvas(box, width=360, height=360, highlightthickness=0)
        sb = ttk.Scrollbar(box, command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.vars = {}
        for t, c in cols:
            key = f"{t}.{c}"
            self.vars[key] = tk.BooleanVar(value=key in selected)
            ttk.Checkbutton(inner, text=key, variable=self.vars[key]).pack(anchor="w")
        bt = ttk.Frame(self)
        bt.pack(pady=8)
        ttk.Button(bt, text="OK", command=self.ok).pack(side="left", padx=4)
        ttk.Button(bt, text="Annulla", command=self.destroy).pack(side="left")
        self.transient(master)
        self.grab_set()

    def ok(self):
        self.result = [k for k, v in self.vars.items() if v.get()]
        self.destroy()


class App(tk.Tk):
    HEAD = [("tipo", "Tipo doc", 70), ("num", "Numero", 70), ("data", "Data", 85), ("cf", "Codice", 80),
            ("rs", "Ragione sociale", 220), ("campo", "Campo", 130), ("testo", "Testo trovato", 520)]

    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1200x720")
        self.cfg, self.password, self.cn = load_config(), "", None
        self.cols = []
        self.q = queue.Queue()
        self.fulltext = {}
        self.build_ui()
        self.after(100, self.poll)
        self.after(200, self.do_connect)

    def build_ui(self):
        self.configure(bg=WHITE)
        st = ttk.Style(self)
        try:
            st.theme_use("vista" if "vista" in st.theme_names() else "clam")
        except tk.TclError:
            pass
        st.configure(".", font=(FONT, 10))
        st.configure("Treeview", font=(FONT, 10), rowheight=26, background=WHITE, fieldbackground=WHITE,
                     borderwidth=0)
        st.configure("Treeview.Heading", font=(FONT, 10, "bold"), background=BAR, relief="flat")
        st.map("Treeview", background=[("selected", "#cce4f7")], foreground=[("selected", "black")])

        body = tk.Frame(self, bg=WHITE)
        body.pack(fill="both", expand=True, padx=20, pady=(14, 0))

        self.link = tk.Label(body, text="Vedere le informazioni sulla connessione", fg=LINK, bg=WHITE,
                             font=(FONT, 10), cursor="hand2")
        self.link.pack(anchor="w", pady=(0, 10))
        self.link.bind("<Button-1>", lambda e: self.do_connect())

        # schede riepilogo (stile "Informazioni su" di Windows)
        self.cards = {}
        row = tk.Frame(body, bg=WHITE)
        row.pack(fill="x")
        for i, (key, title) in enumerate([("conn", "Connessione"), ("db", "Database"),
                                          ("cols", "Campi in ricerca"), ("res", "Risultati")]):
            row.columnconfigure(i, weight=1, uniform="c")
            box = tk.Frame(row, bg=WHITE, highlightbackground=BORDER, highlightthickness=1)
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            tk.Label(box, text=title, bg=WHITE, fg="#444", font=(FONT, 9)).pack(anchor="w", padx=14, pady=(10, 0))
            big = tk.Label(box, text="-", bg=WHITE, font=(FONT, 13, "bold"), justify="left",
                           anchor="w", wraplength=210)
            big.pack(anchor="w", padx=14, pady=(8, 0))
            small = tk.Label(box, text="", bg=WHITE, fg=GREY, font=(FONT, 9), anchor="w")
            small.pack(anchor="w", padx=14, pady=(14, 10))
            self.cards[key] = (big, small)

        # barra grigia: ricerca
        bar = tk.Frame(body, bg=BAR, highlightbackground=BORDER, highlightthickness=1)
        bar.pack(fill="x", pady=(10, 0))
        self.v_term = tk.StringVar()
        tk.Label(bar, text="Cerca nelle note", bg=BAR, font=(FONT, 11, "bold")).grid(
            row=0, column=0, padx=14, pady=12, sticky="w")
        e = tk.Entry(bar, textvariable=self.v_term, font=(FONT, 11), relief="flat",
                     highlightbackground=BORDER, highlightthickness=1)
        e.grid(row=0, column=1, sticky="ew", ipady=4, padx=(0, 10))
        e.bind("<Return>", lambda ev: self.search())
        e.focus_set()
        bar.columnconfigure(1, weight=1)
        self.btn = tk.Button(bar, text="Cerca", command=self.search, bg=BTN, activebackground=BTN_HOVER,
                             relief="flat", font=(FONT, 10), width=14, pady=4, cursor="hand2")
        self.btn.grid(row=0, column=2, padx=14)

        # filtri (etichetta in grassetto / valore)
        flt = tk.Frame(body, bg=BAR, highlightbackground=BORDER, highlightthickness=1)
        flt.pack(fill="x", pady=(8, 0))
        self.v_scope = tk.StringVar(value="tutti")
        self.v_from, self.v_to = tk.StringVar(), tk.StringVar()
        tk.Label(flt, text="Filtri", bg=BAR, font=(FONT, 10, "bold")).grid(row=0, column=0, padx=14, pady=8, sticky="w")
        sc = tk.Frame(flt, bg=BAR)
        sc.grid(row=0, column=1, sticky="w")
        for txt, val in [("Clienti", "clienti"), ("Fornitori", "fornitori"), ("Entrambi", "tutti")]:
            tk.Radiobutton(sc, text=txt, value=val, variable=self.v_scope, bg=BAR, activebackground=BAR,
                           font=(FONT, 10), command=self.refresh_cards).pack(side="left", padx=(0, 12))
        tk.Label(flt, text="Periodo (AAAA-MM-GG)", fg=GREY, bg=BAR, font=(FONT, 10)).grid(row=0, column=2, padx=(30, 6))
        for var in (self.v_from, self.v_to):
            tk.Entry(flt, textvariable=var, width=12, font=(FONT, 10), relief="flat",
                     highlightbackground=BORDER, highlightthickness=1).grid(
                row=0, column=3 if var is self.v_from else 5, ipady=2, padx=2)
        tk.Label(flt, text="–", bg=BAR).grid(row=0, column=4)
        for col, (txt, cmd) in enumerate([("Campi…", self.pick_cols), ("Connessione…", self.do_connect),
                                          ("Esporta CSV", self.export)]):
            tk.Button(flt, text=txt, command=cmd, bg=BTN, activebackground=BTN_HOVER, relief="flat",
                      font=(FONT, 9), width=12, cursor="hand2").grid(row=0, column=6 + col, padx=(8 if col else 24, 0), pady=6)
        flt.columnconfigure(9, minsize=14)

        # risultati
        tk.Label(body, text="Risultati", bg=WHITE, font=(FONT, 16)).pack(anchor="w", pady=(14, 4))
        wrap = tk.Frame(body, bg=WHITE, highlightbackground=BORDER, highlightthickness=1)
        wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(wrap, columns=[h[0] for h in self.HEAD], show="headings")
        for key, label, w in self.HEAD:
            self.tree.heading(key, text=label, anchor="w")
            self.tree.column(key, width=w, anchor="w")
        sb = ttk.Scrollbar(wrap, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self.show_full)
        self.status = tk.StringVar(value="Non connesso")
        tk.Label(self, textvariable=self.status, bg=WHITE, fg=GREY, anchor="w", font=(FONT, 9)).pack(
            fill="x", padx=20, pady=4)
        self.nres = None
        self.refresh_cards()

    def refresh_cards(self):
        scope = {"clienti": "Solo clienti", "fornitori": "Solo fornitori", "tutti": "Clienti e fornitori"}
        on = self.cn is not None
        self.cards["conn"][0].config(text="Connesso" if on else "Non connesso", fg="#107c10" if on else "#c42b1c")
        self.cards["conn"][1].config(text=self.cfg["server"])
        self.cards["db"][0].config(text=self.cfg["database"] or "-")
        self.cards["db"][1].config(text="Arca Evolution (sola lettura)")
        self.cards["cols"][0].config(text=f"{len(self.cfg['columns'])} campi")
        self.cards["cols"][1].config(text=scope[self.v_scope.get()])
        self.cards["res"][0].config(text="-" if self.nres is None else str(self.nres))
        self.cards["res"][1].config(text="documenti con il testo cercato")

    # --- connessione ---
    def do_connect(self):
        if pyodbc is None:
            messagebox.showerror(APP_NAME, "Modulo pyodbc mancante (pip install pyodbc).")
            return
        dlg = ConnectDialog(self, self.cfg)
        self.wait_window(dlg)
        if not dlg.ok:
            return
        try:
            if self.cn:
                self.cn.close()
            self.cn = connect(self.cfg, dlg.password)
            self.cols = discover_columns(self.cn)
            if not self.cols:
                raise RuntimeError(f"Tabelle {T_TESTATA}/{T_RIGHE} non trovate: è il database di Arca Evolution?")
        except Exception as e:
            self.cn = None
            self.status.set("Non connesso")
            self.refresh_cards()
            messagebox.showerror(APP_NAME, f"Connessione fallita:\n{e}")
            return
        valid = {f"{t}.{c}" for t, c in self.cols}
        self.cfg["columns"] = [c for c in self.cfg["columns"] if c in valid] or default_selection(self.cols)
        save_config(self.cfg)
        self.status.set(f"Connesso a {self.cfg['server']} / {self.cfg['database']}")
        self.refresh_cards()

    def pick_cols(self):
        if not self.cols:
            messagebox.showinfo(APP_NAME, "Connettiti prima al database.")
            return
        d = ColumnsDialog(self, self.cols, self.cfg["columns"])
        self.wait_window(d)
        if d.result is not None:
            self.cfg["columns"] = d.result
            save_config(self.cfg)
            self.status.set(f"{len(d.result)} campi in ricerca")
            self.refresh_cards()

    # --- ricerca ---
    def search(self):
        term = self.v_term.get().strip()
        if not self.cn:
            self.do_connect()
            return
        if not term:
            return
        if not self.cfg["columns"]:
            messagebox.showinfo(APP_NAME, "Nessun campo selezionato (pulsante 'Campi…').")
            return
        df, dt = self.v_from.get().strip(), self.v_to.get().strip()
        if bool(df) != bool(dt):
            messagebox.showinfo(APP_NAME, "Indica entrambe le date (Dal e Al) oppure nessuna.")
            return
        self.tree.delete(*self.tree.get_children())
        self.fulltext.clear()
        self.nres = None
        self.refresh_cards()
        self.btn.config(state="disabled", text="Ricerca…")
        self.status.set("Ricerca in corso…")
        args = (term, self.v_scope.get(), (df, dt) if df else None, list(self.cfg["columns"]))
        threading.Thread(target=self.worker, args=args, daemon=True).start()

    def worker(self, term, scope, dates, columns):
        try:
            cur = self.cn.cursor()
            like = f"%{esc_like(term)}%"
            n = 0
            for key in columns:
                table, col = key.split(".", 1)
                sql = build_query(table, col, scope, bool(dates), self.cfg["ignore_accents"])
                params = [like] + (list(dates) if dates else [])
                for r in cur.execute(sql, params):
                    n += 1
                    self.q.put(("row", key, term, r))
            self.q.put(("done", n))
        except Exception as e:
            self.q.put(("err", str(e)))

    def poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                if m[0] == "row":
                    _, key, term, r = m
                    tipo, num, data, cf, rs, testo = r
                    iid = self.tree.insert("", "end", values=(
                        tipo, num, str(data)[:10] if data else "", cf, rs, key, snippet(testo, term)))
                    self.fulltext[iid] = (f"{tipo} n. {num} del {str(data)[:10]} - {cf} {rs or ''}\n"
                                          f"Campo: {key}\n\n{testo}")
                elif m[0] == "done":
                    self.btn.config(state="normal", text="Cerca")
                    self.status.set(f"{m[1]} risultati")
                    self.nres = m[1]
                    self.refresh_cards()
                elif m[0] == "err":
                    self.btn.config(state="normal", text="Cerca")
                    self.status.set("Errore")
                    messagebox.showerror(APP_NAME, f"Errore nella ricerca:\n{m[1]}")
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def show_full(self, _):
        sel = self.tree.selection()
        if not sel:
            return
        w = tk.Toplevel(self)
        w.title("Dettaglio")
        t = tk.Text(w, wrap="word", width=90, height=24)
        t.insert("1.0", self.fulltext.get(sel[0], ""))
        t.config(state="disabled")
        t.pack(fill="both", expand=True)

    def export(self):
        rows = [self.tree.item(i, "values") for i in self.tree.get_children()]
        if not rows:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([h[1] for h in self.HEAD])
            w.writerows(rows)
        self.status.set(f"Esportati {len(rows)} risultati in {path}")


if __name__ == "__main__":
    App().mainloop()
