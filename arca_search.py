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
        self.geometry("1200x640")
        self.cfg, self.password, self.cn = load_config(), "", None
        self.cols = []
        self.q = queue.Queue()
        self.fulltext = {}
        self.build_ui()
        self.after(100, self.poll)
        self.after(200, self.do_connect)

    def build_ui(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        self.v_term = tk.StringVar()
        self.v_scope = tk.StringVar(value="tutti")
        self.v_from, self.v_to = tk.StringVar(), tk.StringVar()
        ttk.Label(top, text="Cerca:").grid(row=0, column=0)
        e = ttk.Entry(top, textvariable=self.v_term, width=40)
        e.grid(row=0, column=1, padx=4)
        e.bind("<Return>", lambda ev: self.search())
        e.focus_set()
        for i, (txt, val) in enumerate([("Clienti", "clienti"), ("Fornitori", "fornitori"), ("Entrambi", "tutti")]):
            ttk.Radiobutton(top, text=txt, value=val, variable=self.v_scope).grid(row=0, column=2 + i, padx=4)
        ttk.Label(top, text="Dal (AAAA-MM-GG):").grid(row=1, column=0, sticky="e", pady=4)
        ttk.Entry(top, textvariable=self.v_from, width=12).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(top, text="Al:").grid(row=1, column=2, sticky="e")
        ttk.Entry(top, textvariable=self.v_to, width=12).grid(row=1, column=3, sticky="w")
        self.btn = ttk.Button(top, text="Cerca", command=self.search)
        self.btn.grid(row=0, column=5, padx=8)
        ttk.Button(top, text="Esporta CSV", command=self.export).grid(row=1, column=5, padx=8)
        ttk.Button(top, text="Connessione…", command=self.do_connect).grid(row=0, column=6)
        ttk.Button(top, text="Campi…", command=self.pick_cols).grid(row=1, column=6)

        self.tree = ttk.Treeview(self, columns=[h[0] for h in self.HEAD], show="headings")
        for key, label, w in self.HEAD:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=w, anchor="w")
        sb = ttk.Scrollbar(self, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True, padx=(8, 0))
        self.tree.bind("<Double-1>", self.show_full)
        self.status = tk.StringVar(value="Non connesso")
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w").pack(fill="x", side="bottom")

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
            messagebox.showerror(APP_NAME, f"Connessione fallita:\n{e}")
            return
        valid = {f"{t}.{c}" for t, c in self.cols}
        self.cfg["columns"] = [c for c in self.cfg["columns"] if c in valid] or default_selection(self.cols)
        save_config(self.cfg)
        self.status.set(f"Connesso a {self.cfg['server']} / {self.cfg['database']} - "
                        f"{len(self.cfg['columns'])} campi in ricerca")

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
        self.btn.config(state="disabled")
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
                    self.btn.config(state="normal")
                    self.status.set(f"{m[1]} risultati")
                elif m[0] == "err":
                    self.btn.config(state="normal")
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
