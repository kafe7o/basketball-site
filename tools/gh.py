"""GitHub от лаптопа - ключът е само в .env и никога не се печата.

    python tools/gh.py create            създава хранилището kafe7o/basketball-site (публично) и включва GitHub Pages от docs/
    python tools/gh.py secret NAME       слага тайна от .env в хранилището (шифрована с публичния му ключ)
    python tools/gh.py push              git push към GitHub с ключа (без да го записва в git)
    python tools/gh.py run               пуска цикъла в облака сега
    python tools/gh.py status            последните пускания на цикъла
"""

import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hoops import config                                  # noqa: E402

API = "https://api.github.com"


def api(method, path, body=None):
    token = config.key("GITHUB_TOKEN")
    if not token:
        raise SystemExit("няма GITHUB_TOKEN в .env")
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, json.loads(raw) if raw else {}


def create():
    owner, name = config.REPO.split("/")
    st, body = api("POST", "/user/repos", {"name": name, "private": False, "description": "Баскетболният робот - НБА и Евролига, честно мерене срещу пазара",
                                           "has_issues": False, "has_wiki": False, "auto_init": False})
    print("хранилище:", st, body.get("full_name") or body.get("message") or body.get("errors"))


def pages():
    st, body = api("POST", f"/repos/{config.REPO}/pages", {"source": {"branch": "main", "path": "/docs"}})
    print("GitHub Pages:", st, body.get("html_url") or body.get("message"))


def secret(name):
    from nacl import encoding, public
    value = config.key(name)
    if not value:
        raise SystemExit(f"{name} няма в .env")
    st, pk = api("GET", f"/repos/{config.REPO}/actions/secrets/public-key")
    if st != 200:
        raise SystemExit(f"публичният ключ не се взе: {st}")
    sealed = public.SealedBox(public.PublicKey(pk["key"].encode(), encoding.Base64Encoder())).encrypt(value.encode())
    st, _ = api("PUT", f"/repos/{config.REPO}/actions/secrets/{name}", {"encrypted_value": base64.b64encode(sealed).decode(), "key_id": pk["key_id"]})
    print(f"тайната {name}: {'записана' if st in (201, 204) else 'ГРЕШКА ' + str(st)} (стойността не се показва)")


def push():
    token = config.key("GITHUB_TOKEN")
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    r = subprocess.run(["git", "-c", f"http.extraheader=AUTHORIZATION: basic {auth}", "push", "-u", "origin", "main"], cwd=ROOT,
                       capture_output=True, text=True)
    print((r.stdout + r.stderr).replace(token, "***").replace(auth, "***"))
    if r.returncode:
        raise SystemExit(r.returncode)


def pull():
    token = config.key("GITHUB_TOKEN")
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    r = subprocess.run(["git", "-c", f"http.extraheader=AUTHORIZATION: basic {auth}", "pull", "--rebase", "origin", "main"], cwd=ROOT,
                       capture_output=True, text=True)
    print((r.stdout + r.stderr).replace(token, "***").replace(auth, "***"))


def run():
    st, _ = api("POST", f"/repos/{config.REPO}/actions/workflows/robot.yml/dispatches", {"ref": "main"})
    print("пускане:", st)


def status():
    st, body = api("GET", f"/repos/{config.REPO}/actions/workflows/robot.yml/runs?per_page=5")
    for r in body.get("workflow_runs", []):
        print(r["id"], r["created_at"], r["status"], r["conclusion"])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"create": lambda: (create(), pages()), "pages": pages, "secret": lambda: secret(sys.argv[2]), "push": push, "pull": pull,
     "run": run, "status": status}.get(cmd, lambda: print(__doc__))()
