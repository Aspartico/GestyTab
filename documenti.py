"""ArcaNote - nuovo documento (DDT, fattura, nota di credito...) da una richiesta scritta o dettata.

FASE 1: interpreta la richiesta con Claude, cerca cliente/fornitore e articoli nel database di Arca
(SOLO LETTURA) e mostra un'anteprima modificabile. NON scrive nulla in Arca.
Al modello vengono inviati soltanto il testo della richiesta e l'elenco dei tipi documento.
"""
import base64
import datetime
import html
import json
import os
import queue
import re
import tempfile
import threading
import urllib.error
import urllib.request
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

try:
    import pyodbc
except ImportError:
    pyodbc = None

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b"
MODEL = "claude-opus-5-5"
FALLBACK_MODEL = "claude-opus-4-8"

FONT = "Segoe UI"
WHITE, BAR, BORDER, GREY = "#ffffff", "#f2f2f2", "#e5e5e5", "#707070"
BTN, BTN_HOVER = "#cccccc", "#b8b8b8"

SYSTEM = """Sei l'assistente di un gestionale (Arca Evolution). Ricevi una richiesta, scritta o dettata a voce in italiano, di preparare un documento commerciale. Estrai i dati e rispondi SOLO con un oggetto JSON, senza altro testo, con questi campi:
- "tipo_documento": il codice scelto SOLO tra quelli elencati sotto, oppure null se non e' chiaro;
- "controparte": "cliente" oppure "fornitore";
- "nome_controparte": nome del cliente o fornitore come detto, oppure null;
- "righe": lista di {"descrizione": articolo come detto, "quantita": numero oppure null, "prezzo": numero oppure null};
- "data": "AAAA-MM-GG" oppure null se non detta;
- "note": testo oppure null;
- "dubbi": lista di brevi frasi in italiano su cio' che e' ambiguo o mancante.
Non inventare nulla: se un dato non e' nella richiesta usa null. Nelle richieste dettate a voce correggi i probabili errori di trascrizione. Oggi e' @OGGI@.
Tipi di documento disponibili (codice - descrizione):
@TIPI@"""


# --- chiave API: salvata cifrata per l'utente Windows (DPAPI) ---------------------------------
def _dpapi(data, protect):
    import ctypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def encode_key(key):
    try:
        return "dpapi:" + base64.b64encode(_dpapi(key.encode(), True)).decode()
    except Exception:
        return "plain:" + base64.b64encode(key.encode()).decode()


def decode_key(blob):
    try:
        kind, _, val = blob.partition(":")
        raw = base64.b64decode(val)
        return (_dpapi(raw, False) if kind == "dpapi" else raw).decode()
    except Exception:
        return ""


# --- interpretazione ------------------------------------------------------------------------
def parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("risposta non interpretabile")
    return json.loads(m.group(0))


def interpret(api_key, richiesta, tipi):
    import anthropic
    elenco = "\n".join(f"{c} - {d}" if d else c for c, d in tipi) or "(elenco non disponibile)"
    system = (SYSTEM.replace("@OGGI@", datetime.date.today().isoformat()).replace("@TIPI@", elenco))
    client = anthropic.Anthropic(api_key=api_key)
    kwargs = dict(model=MODEL, max_tokens=2000, system=system, output_config={"effort": "low"},
                  messages=[{"role": "user", "content": richiesta}])
    try:
        try:  # se il modello rifiuta, l'API riprova da sola con un modello di riserva
            r = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                            fallbacks=[{"model": FALLBACK_MODEL}], **kwargs)
        except anthropic.BadRequestError:
            r = client.messages.create(**kwargs)
    except anthropic.AuthenticationError:
        raise PermissionError("Chiave API non valida.")
    except anthropic.RateLimitError:
        raise RuntimeError("Troppe richieste o credito esaurito sull'account Anthropic. Riprova tra poco.")
    except anthropic.APIConnectionError:
        raise RuntimeError("Nessuna connessione a Internet.")
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Errore del servizio ({e.status_code}): {e.message}")
    if r.stop_reason == "refusal":
        raise RuntimeError("La richiesta è stata rifiutata dal servizio. Riformulala.")
    return parse_json("".join(b.text for b in r.content if b.type == "text"))


# --- IA locale (Ollama) -----------------------------------------------------------------------
_RIGA = {"type": "object", "required": ["descrizione"], "properties": {
    "descrizione": {"type": "string"}, "quantita": {"type": ["number", "null"]},
    "prezzo": {"type": ["number", "null"]}}}
SCHEMA = {"type": "object", "required": ["controparte", "righe"], "properties": {
    "tipo_documento": {"type": ["string", "null"]},
    "controparte": {"type": "string", "enum": ["cliente", "fornitore"]},
    "nome_controparte": {"type": ["string", "null"]},
    "righe": {"type": "array", "items": _RIGA},
    "data": {"type": ["string", "null"]}, "note": {"type": ["string", "null"]},
    "dubbi": {"type": "array", "items": {"type": "string"}}}}


def _system_prompt(tipi):
    elenco = "\n".join(f"{c} - {d}" if d else c for c, d in tipi) or "(elenco non disponibile)"
    return SYSTEM.replace("@OGGI@", datetime.date.today().isoformat()).replace("@TIPI@", elenco)


def _ollama(url, path, payload=None, timeout=180):
    req = urllib.request.Request(url.rstrip("/") + path, method="POST" if payload is not None else "GET",
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        if e.code == 404:
            raise RuntimeError("Modello non scaricato: esegui 'ollama pull " + str((payload or {}).get("model", "")) + "'.")
        raise RuntimeError(f"Ollama ha risposto {e.code}: {body[:200]}")
    except (urllib.error.URLError, OSError):
        raise RuntimeError(f"Ollama non raggiungibile su {url}. Controlla che sia avviato.")


def interpret_local(url, model, richiesta, tipi):
    j = _ollama(url, "/api/chat", {
        "model": model, "stream": False, "format": SCHEMA, "options": {"temperature": 0},
        "messages": [{"role": "system", "content": _system_prompt(tipi)},
                     {"role": "user", "content": richiesta}]})
    d = parse_json(j.get("message", {}).get("content", ""))
    if not isinstance(d.get("righe"), list):
        raise ValueError("risposta incompleta")
    return d


def interpret_any(cfg, richiesta, tipi):
    """Ritorna (dati, motore_usato). 'auto': prima IA locale, poi Claude se serve."""
    motore = cfg.get("ia_motore", "auto")
    local_err = None
    if motore in ("locale", "auto"):
        try:
            return interpret_local(cfg.get("ia_url") or OLLAMA_URL, cfg.get("ia_modello") or OLLAMA_MODEL,
                                   richiesta, tipi), "IA locale"
        except Exception as e:
            local_err = e
            if motore == "locale":
                raise RuntimeError(str(e))
    key = decode_key(cfg["api_key_enc"]) if cfg.get("api_key_enc") else ""
    if not key:
        extra = f"IA locale non disponibile ({local_err}) e " if local_err else ""
        raise RuntimeError(extra + "nessuna chiave Claude impostata (pulsante 'Chiave Claude…').")
    return interpret(key, richiesta, tipi), "Claude"


# --- ricerche in Arca (SOLA LETTURA) ----------------------------------------------------------
def _esc(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("[", "\\[")


def _words(text):
    return [w for w in re.split(r"\W+", text or "") if len(w) > 1]


def list_doc_types(cn):
    """[(codice, descrizione)] dei tipi documento. Prima la tabella DO, altrimenti i codici in uso."""
    cur = cn.cursor()
    try:
        return [(r[0], r[1] or "") for r in cur.execute("SELECT Cd_DO, Descrizione FROM [DO] ORDER BY Cd_DO")]
    except pyodbc.Error:
        return [(r[0], "") for r in cur.execute(
            "SELECT Cd_DO FROM DOTes GROUP BY Cd_DO ORDER BY COUNT(*) DESC")]


def find_cf(cn, name, controparte):
    words = _words(name)
    if not words:
        return []
    cond = " AND ".join(["Descrizione COLLATE Latin1_General_CI_AI LIKE ? ESCAPE '\\'"] * len(words))
    params = [f"%{_esc(w)}%" for w in words]
    cur = cn.cursor()
    flag = "Fornitore" if controparte == "fornitore" else "Cliente"
    try:
        rows = cur.execute(f"SELECT TOP 15 Cd_CF, Descrizione FROM CF WHERE {cond} AND [{flag}] = 1 "
                           f"ORDER BY Descrizione", params).fetchall()
    except pyodbc.Error:
        rows = cur.execute(f"SELECT TOP 15 Cd_CF, Descrizione FROM CF WHERE {cond} ORDER BY Descrizione",
                           params).fetchall()
    return [(r[0], r[1] or "") for r in rows]


def find_ar(cn, text):
    words = _words(text)
    if not words:
        return []
    cur = cn.cursor()
    like = "COLLATE Latin1_General_CI_AI LIKE ? ESCAPE '\\'"
    for joiner in (" AND ", " OR "):  # prima tutte le parole, poi almeno una
        cond = joiner.join([f"(Descrizione {like} OR Cd_AR {like})"] * len(words))
        params = [p for w in words for p in (f"%{_esc(w)}%",) * 2]
        rows = cur.execute(f"SELECT TOP 15 Cd_AR, Descrizione FROM AR WHERE {cond} ORDER BY Cd_AR",
                           params).fetchall()
        if rows:
            return [(r[0], r[1] or "") for r in rows]
    return []


def num(v):
    try:
        f = float(str(v).replace(",", "."))
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return None


def label(cd, descr):
    return f"{cd} - {descr}" if descr else str(cd)


def code_of(text):
    return text.split(" - ", 1)[0].strip()


# --- finestra ---------------------------------------------------------------------------------
class NuovoDocumento(tk.Toplevel):
    def __init__(self, app, cn, cfg, save_cfg):
        super().__init__(app)
        self.title("Nuovo documento - ArcaNote")
        self.geometry("960x760")
        self.configure(bg=WHITE)
        self.cn, self.cfg, self.save_cfg = cn, cfg, save_cfg
        self.q = queue.Queue()
        self.righe, self.cf_cands = [], []
        self.tipi, err = [], ""
        try:
            self.tipi = list_doc_types(cn)
        except Exception as e:
            err = f" (tipi documento non letti: {e})"
        self.build(err)

    # -- interfaccia
    def lbl(self, parent, text, **kw):
        return tk.Label(parent, text=text, bg=kw.pop("bg", WHITE), font=kw.pop("font", (FONT, 10)), **kw)

    def btn(self, parent, text, cmd, **kw):
        return tk.Button(parent, text=text, command=cmd, bg=BTN, activebackground=BTN_HOVER, relief="flat",
                         font=(FONT, 10), cursor="hand2", **kw)

    def build(self, err):
        body = tk.Frame(self, bg=WHITE)
        body.pack(fill="both", expand=True, padx=20, pady=14)

        self.lbl(body, "Cosa devo preparare?", font=(FONT, 14, "bold")).pack(anchor="w")
        self.lbl(body, "Scrivi la richiesta, oppure dettala con la voce di Windows (premi Win+H nel riquadro "
                 "e parla). Es.: \"DDT per Rossi Mario, 10 batterie 12V 100Ah, consegna domani\"",
                 fg=GREY, wraplength=880, justify="left").pack(anchor="w", pady=(2, 6))
        self.txt = tk.Text(body, height=4, font=(FONT, 11), relief="flat", highlightthickness=1,
                           highlightbackground=BORDER, highlightcolor=BORDER, wrap="word")
        self.txt.pack(fill="x")
        self.txt.focus_set()
        bar = tk.Frame(body, bg=WHITE)
        bar.pack(fill="x", pady=6)
        self.b_int = self.btn(bar, "Interpreta", self.interpreta, width=16, pady=4)
        self.b_int.pack(side="left")
        self.btn(bar, "Motore IA…", self.motore_dialog, width=12).pack(side="left", padx=8)
        self.btn(bar, "Chiave Claude…", self.ask_key, width=13).pack(side="left")
        self.status = tk.StringVar(value="Pronto." + err)
        self.lbl(bar, "", textvariable=self.status, fg=GREY).pack(side="left", padx=10)

        self.prev = tk.Frame(body, bg=BAR, highlightbackground=BORDER, highlightthickness=1)
        self.prev.pack(fill="both", expand=True, pady=(6, 0))
        p = tk.Frame(self.prev, bg=BAR)
        p.pack(fill="both", expand=True, padx=14, pady=10)
        tk.Label(p, text="ANTEPRIMA - non ancora inviata ad Arca", bg=BAR, fg="#c42b1c",
                 font=(FONT, 10, "bold")).grid(row=0, column=0, columnspan=4, sticky="w")

        def row(r, text):
            tk.Label(p, text=text, bg=BAR, font=(FONT, 10, "bold")).grid(row=r, column=0, sticky="e", pady=3, padx=(0, 8))

        row(1, "Documento")
        self.v_tipo = tk.StringVar()
        self.cb_tipo = ttk.Combobox(p, textvariable=self.v_tipo, width=46,
                                    values=[label(c, d) for c, d in self.tipi])
        self.cb_tipo.grid(row=1, column=1, sticky="w")
        row(2, "Intestatario")
        self.v_contro = tk.StringVar(value="cliente")
        rf = tk.Frame(p, bg=BAR)
        rf.grid(row=2, column=1, sticky="w")
        for t, v in (("Cliente", "cliente"), ("Fornitore", "fornitore")):
            tk.Radiobutton(rf, text=t, value=v, variable=self.v_contro, bg=BAR, activebackground=BAR,
                           font=(FONT, 10)).pack(side="left", padx=(0, 10))
        self.v_cf = tk.StringVar()
        self.cb_cf = ttk.Combobox(p, textvariable=self.v_cf, width=70)
        self.cb_cf.grid(row=3, column=1, columnspan=3, sticky="w")
        self.cb_cf.bind("<Return>", lambda e: self.cerca_cf())
        self.btn(p, "Cerca", self.cerca_cf, width=8).grid(row=3, column=4, padx=6)
        row(4, "Data")
        self.v_data = tk.StringVar(value=datetime.date.today().isoformat())
        tk.Entry(p, textvariable=self.v_data, width=14, font=(FONT, 10)).grid(row=4, column=1, sticky="w")
        row(5, "Note")
        self.v_note = tk.StringVar()
        tk.Entry(p, textvariable=self.v_note, width=84, font=(FONT, 10)).grid(row=5, column=1, columnspan=4, sticky="w")

        cols = [("rich", "Richiesto", 190), ("cd", "Codice", 110), ("descr", "Articolo trovato", 330),
                ("qta", "Quantità", 70), ("prezzo", "Prezzo", 80)]
        self.tree = ttk.Treeview(p, columns=[c[0] for c in cols], show="headings", height=6)
        for k, t, w in cols:
            self.tree.heading(k, text=t, anchor="w")
            self.tree.column(k, width=w, anchor="w")
        self.tree.grid(row=6, column=0, columnspan=5, sticky="ew", pady=(10, 4))
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        ed = tk.Frame(p, bg=BAR)
        ed.grid(row=7, column=0, columnspan=5, sticky="w")
        self.v_ar, self.v_qta, self.v_prz = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.cb_ar = ttk.Combobox(ed, textvariable=self.v_ar, width=52)
        self.cb_ar.pack(side="left")
        self.cb_ar.bind("<Return>", lambda e: self.cerca_ar())
        tk.Label(ed, text=" Qtà", bg=BAR).pack(side="left")
        tk.Entry(ed, textvariable=self.v_qta, width=7).pack(side="left")
        tk.Label(ed, text=" Prezzo", bg=BAR).pack(side="left")
        tk.Entry(ed, textvariable=self.v_prz, width=9).pack(side="left")
        self.btn(ed, "Applica", self.applica, width=8).pack(side="left", padx=6)
        self.btn(ed, "Nuova riga", self.nuova_riga, width=10).pack(side="left")
        self.btn(ed, "Elimina riga", self.elimina_riga, width=10).pack(side="left", padx=6)

        self.dubbi = tk.Label(p, text="", bg=BAR, fg="#9d5d00", font=(FONT, 10), justify="left", wraplength=860)
        self.dubbi.grid(row=8, column=0, columnspan=5, sticky="w", pady=(8, 0))
        p.columnconfigure(3, weight=1)

        foot = tk.Frame(body, bg=WHITE)
        foot.pack(fill="x", pady=(10, 0))
        self.btn(foot, "Salva anteprima (stampa / PDF)", self.salva_html, pady=4).pack(side="left")
        b = tk.Button(foot, text="Invia ad Arca", state="disabled", relief="flat", font=(FONT, 10))
        b.pack(side="left", padx=10)
        self.lbl(foot, "Invio ad Arca non ancora attivo: per ora il documento si prepara e si controlla qui.",
                 fg=GREY).pack(side="left")

    # -- chiave API
    def api_key(self):
        return decode_key(self.cfg.get("api_key_enc", "")) if self.cfg.get("api_key_enc") else ""

    def ask_key(self):
        k = simpledialog.askstring("Chiave API Anthropic",
                                   "Incolla la chiave API (si crea su console.anthropic.com).\n"
                                   "Viene salvata cifrata, solo per questo utente Windows.",
                                   show="*", parent=self)
        if k and k.strip():
            self.cfg["api_key_enc"] = encode_key(k.strip())
            self.save_cfg()
            return k.strip()
        return ""

    # -- scelta del motore IA
    def motore_dialog(self):
        w = tk.Toplevel(self)
        w.title("Motore IA")
        w.configure(bg=WHITE)
        w.transient(self)
        v_m = tk.StringVar(value=self.cfg.get("ia_motore", "auto"))
        v_u = tk.StringVar(value=self.cfg.get("ia_url") or OLLAMA_URL)
        v_mod = tk.StringVar(value=self.cfg.get("ia_modello") or OLLAMA_MODEL)
        f = tk.Frame(w, bg=WHITE)
        f.pack(padx=16, pady=12)
        for i, (t, v) in enumerate((("Automatico: prima l'IA locale, Claude solo se serve", "auto"),
                                    ("Solo IA locale (Ollama), nessun dato esce dal PC", "locale"),
                                    ("Solo Claude (serve la chiave API)", "claude"))):
            tk.Radiobutton(f, text=t, value=v, variable=v_m, bg=WHITE, activebackground=WHITE,
                           font=(FONT, 10)).grid(row=i, column=0, columnspan=2, sticky="w")
        tk.Label(f, text="Indirizzo Ollama", bg=WHITE).grid(row=3, column=0, sticky="e", pady=(10, 2))
        tk.Entry(f, textvariable=v_u, width=36).grid(row=3, column=1, pady=(10, 2))
        tk.Label(f, text="Modello", bg=WHITE).grid(row=4, column=0, sticky="e")
        tk.Entry(f, textvariable=v_mod, width=36).grid(row=4, column=1)
        res = tk.StringVar()
        tk.Label(f, textvariable=res, bg=WHITE, fg=GREY, wraplength=380, justify="left").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=6)

        def prova():
            try:
                names = [m["name"] for m in _ollama(v_u.get(), "/api/tags", timeout=10).get("models", [])]
                ok = any(n == v_mod.get() or n.split(":")[0] == v_mod.get() for n in names)
                res.set(("Collegato. Modello presente." if ok else "Collegato, ma il modello non è scaricato.")
                        + " Disponibili: " + (", ".join(names) or "nessuno"))
            except Exception as e:
                res.set(str(e))

        def salva():
            self.cfg.update(ia_motore=v_m.get(), ia_url=v_u.get().strip(), ia_modello=v_mod.get().strip())
            self.save_cfg()
            w.destroy()
        bt = tk.Frame(f, bg=WHITE)
        bt.grid(row=5, column=0, columnspan=2, pady=(8, 0))
        self.btn(bt, "Prova collegamento", prova, width=18).pack(side="left", padx=4)
        self.btn(bt, "Salva", salva, width=10).pack(side="left")

    # -- interpretazione
    def interpreta(self):
        testo = self.txt.get("1.0", "end").strip()
        if not testo:
            return
        if self.cfg.get("ia_motore") == "claude" and not (self.api_key() or self.ask_key()):
            return
        self.b_int.config(state="disabled")
        self.status.set("Interpretazione in corso…")
        threading.Thread(target=self._worker, args=(testo,), daemon=True).start()
        self.after(150, self._poll)

    def _worker(self, testo):
        try:
            self.q.put(("ok", interpret_any(self.cfg, testo, self.tipi)))
        except PermissionError as e:
            self.q.put(("key", str(e)))
        except Exception as e:
            self.q.put(("err", str(e)))

    def _poll(self):
        try:
            kind, val = self.q.get_nowait()
        except queue.Empty:
            self.after(150, self._poll)
            return
        self.b_int.config(state="normal")
        if kind == "ok":
            dati, motore = val
            self.status.set(f"Interpretato con {motore}. Controlla i dati e correggi dove serve.")
            self.riempi(dati)
        elif kind == "key":
            self.cfg.pop("api_key_enc", None)
            self.save_cfg()
            self.status.set("Chiave non valida.")
            messagebox.showerror("ArcaNote", f"{val}\nInseriscila di nuovo con 'Chiave API…'.", parent=self)
        else:
            self.status.set("Errore.")
            messagebox.showerror("ArcaNote", f"Interpretazione non riuscita:\n{val}", parent=self)

    def riempi(self, d):
        codes = {c: label(c, de) for c, de in self.tipi}
        self.v_tipo.set(codes.get(d.get("tipo_documento"), ""))
        self.v_contro.set("fornitore" if d.get("controparte") == "fornitore" else "cliente")
        self.v_cf.set(d.get("nome_controparte") or "")
        self.cerca_cf()
        self.v_data.set(d.get("data") or datetime.date.today().isoformat())
        self.v_note.set(d.get("note") or "")
        self.righe = []
        for r in d.get("righe") or []:
            riga = {"rich": r.get("descrizione") or "", "cd": "", "descr": "", "cands": [],
                    "qta": num(r.get("quantita")), "prezzo": num(r.get("prezzo"))}
            self.cerca_riga(riga, riga["rich"])
            self.righe.append(riga)
        self.refresh_tree()
        dubbi = list(d.get("dubbi") or [])
        if not self.v_tipo.get():
            dubbi.append("Tipo di documento da scegliere.")
        self.dubbi.config(text=("Da verificare:\n- " + "\n- ".join(dubbi)) if dubbi else "")

    # -- cliente / articoli
    def cerca_cf(self):
        try:
            self.cf_cands = find_cf(self.cn, self.v_cf.get(), self.v_contro.get())
        except Exception as e:
            messagebox.showerror("ArcaNote", f"Ricerca anagrafica non riuscita:\n{e}", parent=self)
            return
        self.cb_cf["values"] = [label(c, d) for c, d in self.cf_cands]
        if len(self.cf_cands) == 1:
            self.v_cf.set(label(*self.cf_cands[0]))
        elif self.cf_cands:
            self.status.set(f"{len(self.cf_cands)} intestatari trovati: scegline uno dall'elenco.")
        else:
            self.status.set("Nessun intestatario trovato: correggi il nome e premi Cerca.")

    def cerca_riga(self, riga, testo):
        try:
            riga["cands"] = find_ar(self.cn, testo)
        except Exception as e:
            riga["cands"] = []
            self.status.set(f"Ricerca articoli non riuscita: {e}")
        if len(riga["cands"]) == 1:
            riga["cd"], riga["descr"] = riga["cands"][0]
        else:
            riga["cd"] = ""
            riga["descr"] = (f"← scegli ({len(riga['cands'])} proposte)" if riga["cands"]
                             else "← nessun articolo trovato")

    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.righe):
            self.tree.insert("", "end", iid=str(i), values=(
                r["rich"], r["cd"], r["descr"], "" if r["qta"] is None else r["qta"],
                "" if r["prezzo"] is None else r["prezzo"]))

    def sel(self):
        s = self.tree.selection()
        return self.righe[int(s[0])] if s else None

    def on_select(self, _=None):
        r = self.sel()
        if not r:
            return
        self.cb_ar["values"] = [label(c, d) for c, d in r["cands"]]
        self.v_ar.set(label(r["cd"], r["descr"]) if r["cd"] else r["rich"])
        self.v_qta.set("" if r["qta"] is None else r["qta"])
        self.v_prz.set("" if r["prezzo"] is None else r["prezzo"])

    def cerca_ar(self):
        r = self.sel()
        if r:
            self.cerca_riga(r, self.v_ar.get())
            self.cb_ar["values"] = [label(c, d) for c, d in r["cands"]]
            self.status.set(f"{len(r['cands'])} articoli trovati: scegli dall'elenco e premi Applica.")

    def applica(self):
        r = self.sel()
        if not r:
            return
        match = [(c, d) for c, d in r["cands"] if label(c, d) == self.v_ar.get()]
        if match:
            r["cd"], r["descr"] = match[0]
        r["qta"], r["prezzo"] = num(self.v_qta.get()), num(self.v_prz.get())
        i = self.righe.index(r)
        self.refresh_tree()
        self.tree.selection_set(str(i))

    def nuova_riga(self):
        self.righe.append({"rich": "", "cd": "", "descr": "", "cands": [], "qta": None, "prezzo": None})
        self.refresh_tree()
        self.tree.selection_set(str(len(self.righe) - 1))
        self.v_ar.set("")
        self.cb_ar.focus_set()

    def elimina_riga(self):
        r = self.sel()
        if r:
            self.righe.remove(r)
            self.refresh_tree()

    # -- anteprima stampabile
    def salva_html(self):
        e = html.escape
        righe = "".join(
            f"<tr><td>{e(str(r['rich']))}</td><td>{e(str(r['cd'])) or '<b style=color:#c42b1c>?</b>'}</td>"
            f"<td>{e(str(r['descr'] if r['cd'] else ''))}</td><td>{e(str(r['qta'] or ''))}</td>"
            f"<td>{e(str(r['prezzo'] or ''))}</td></tr>" for r in self.righe)
        doc = (f"<!doctype html><meta charset=utf-8><title>Anteprima documento</title>"
               f"<style>body{{font-family:Segoe UI,Arial;margin:30px}}table{{border-collapse:collapse;width:100%}}"
               f"td,th{{border:1px solid #ccc;padding:6px;text-align:left}}.b{{color:#c42b1c;font-weight:bold}}</style>"
               f"<p class=b>BOZZA - NON INVIATA AD ARCA</p><h2>{e(self.v_tipo.get() or 'Documento (tipo da scegliere)')}</h2>"
               f"<p><b>{e(self.v_contro.get().capitalize())}:</b> {e(self.v_cf.get())}<br>"
               f"<b>Data:</b> {e(self.v_data.get())}<br><b>Note:</b> {e(self.v_note.get())}</p>"
               f"<table><tr><th>Richiesto</th><th>Codice</th><th>Articolo</th><th>Q.tà</th><th>Prezzo</th></tr>"
               f"{righe}</table>")
        path = os.path.join(tempfile.gettempdir(), "arcanote_anteprima.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(doc)
        webbrowser.open("file:///" + path.replace("\\", "/"))
