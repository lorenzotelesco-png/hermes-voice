"""The File tab: the Obsidian vault, and Hermes' code and logs read-only.

The vault goes through its root-side helper (deploy/vault/hermes-hub-vault),
which does the reading, the searching and every write as a git commit pushed
to GitHub. Hermes' folders go through the dashboard, which runs as root and
would read anything: the hub only ever asks it for paths under FILE_ROOTS, and
checks where the answer really came from.
"""
import base64

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from . import audit, config, control, hermes

router = APIRouter()
MAX_UPLOAD = 15 * 2**20


def error(message, status, **extra):
    return JSONResponse({"error": message, **extra}, status_code=status)


async def ask(action, timeout=90, **fields):
    # Replies carry whole attachments as base64: well over asyncio's default line limit.
    return await control.call(config.VAULT_SOCKET, {"action": action, **fields}, timeout,
                              "L'accesso al vault", limit=32 * 2**20)


async def vault(action, **fields):
    """(answer, None) or (None, the refusal as a response)."""
    try:
        answer = await ask(action, **fields)
    except control.ControlError as e:
        return None, error(str(e), e.status)
    if not answer.pop("ok", False):
        extra = {k: answer[k] for k in ("conflict", "theirs", "theirs_sha") if k in answer}
        return None, error(answer.get("error") or "errore del vault", int(answer.get("status") or 502), **extra)
    return answer, None


async def body(request):
    try:
        data = await request.json()
    except ValueError:
        data = None
    return data if isinstance(data, dict) else {}


# ── vault ─────────────────────────────────────────────────────────
@router.get("/api/vault/index")
async def vault_index():
    answer, refused = await vault("index")
    return refused or answer


@router.get("/api/vault/note")
async def vault_note(path: str = ""):
    answer, refused = await vault("read", path=path)
    return refused or answer


@router.get("/api/vault/raw")
async def vault_raw(path: str = ""):
    answer, refused = await vault("raw", path=path)
    if refused:
        return refused
    return Response(base64.b64decode(answer["data"]), media_type=answer["mime"],
                    headers={"Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"})


@router.get("/api/vault/search")
async def vault_search(q: str = ""):
    answer, refused = await vault("search", q=q)
    return refused or answer


@router.get("/api/vault/status")
async def vault_status():
    answer, refused = await vault("status")
    return refused or answer


@router.post("/api/vault/sync")
async def vault_sync():
    answer, refused = await vault("sync")
    if answer and answer.get("copies"):
        audit.record("pwa", "vault-sync", f"copie di conflitto: {', '.join(answer['copies'])}")
    return refused or answer


@router.post("/api/vault/note")
async def vault_save(request: Request):
    data = await body(request)
    path, text, base = data.get("path"), data.get("text"), data.get("base")
    answer, refused = await vault("save", path=path, text=text, base=base)
    if refused:
        audit.record("pwa", "vault-save", f"path={path} {refused.status_code}", ok=False)
        return refused
    audit.record("pwa", "vault-save", f"path={path} merged={answer['merged']} pushed={answer['pushed']}"
                 + (f" copie={answer['copies']}" if answer["copies"] else ""))
    return answer


@router.post("/api/vault/upload")
async def vault_upload(file: UploadFile = File(None), name: str = Form(None)):
    if file is None:
        return error("nessun file", 400)
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        return error("file troppo grande (massimo 15 MB)", 413)
    answer, refused = await vault("upload", name=name or file.filename or "", data=base64.b64encode(data).decode())
    if refused:
        return refused
    audit.record("pwa", "vault-upload", f"path={answer['path']} bytes={len(data)} pushed={answer['pushed']}")
    return answer


@router.post("/api/vault/capture")
async def vault_capture(request: Request):
    data = await body(request)
    answer, refused = await vault("capture", text=data.get("text"), date=data.get("date"), time=data.get("time"))
    if refused:
        return refused
    audit.record("pwa", "vault-capture", f"path={answer['path']} pushed={answer['pushed']}")
    return answer


# ── Hermes' folders, read-only ────────────────────────────────────
def _absolute(root, rel):
    base = config.FILE_ROOTS[root][1]
    rel = (rel or "").strip("/")
    parts = rel.split("/") if rel else []
    if any(p in ("", ".", "..") for p in parts) or "\\" in rel or "\0" in rel:
        return None
    return "/".join([base, *parts])


def _inside(root, path):
    base = config.FILE_ROOTS[root][1]
    return isinstance(path, str) and (path == base or path.startswith(base + "/"))


@router.get("/api/files/roots")
async def file_roots():
    return {"roots": [{"id": k, "label": label} for k, (label, _) in config.FILE_ROOTS.items()]}


@router.get("/api/files/{root}/list")
async def file_list(root: str, path: str = ""):
    if root not in config.FILE_ROOTS:
        return error("cartella non consentita", 404)
    target = _absolute(root, path)
    if not target:
        return error("percorso non valido", 400)
    d = await hermes.dashboard_get("/api/fs/list", params={"path": target})
    base = config.FILE_ROOTS[root][1]
    # The dashboard resolves symlinks: an entry that lands outside the root is dropped.
    entries = [{"name": e["name"], "dir": bool(e.get("isDirectory")),
                "path": e["path"][len(base) + 1:]}
               for e in d.get("entries", []) if _inside(root, e.get("path")) and e.get("path") != base]
    return {"path": (path or "").strip("/"), "entries": entries, "error": d.get("error")}


@router.get("/api/files/{root}/read")
async def file_read(root: str, path: str = ""):
    if root not in config.FILE_ROOTS:
        return error("cartella non consentita", 404)
    target = _absolute(root, path)
    if not target or target == config.FILE_ROOTS[root][1]:
        return error("percorso non valido", 400)
    d = await hermes.dashboard_get("/api/fs/read-text", params={"path": target})
    if not _inside(root, d.get("path")):
        return error("fuori dalla cartella consentita", 403)
    return {"path": path.strip("/"), "text": "" if d.get("binary") else d.get("text", ""),
            "binary": bool(d.get("binary")), "truncated": bool(d.get("truncated")),
            "size": d.get("byteSize"), "language": d.get("language")}
