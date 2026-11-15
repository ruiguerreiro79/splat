#!/usr/bin/env python3
"""
Splat - pipeline de Gaussian Splatting a partir de video ou fotografias.

  video / pasta de fotos
      -> ffmpeg      (extrair frames)
      -> COLMAP      (poses das camaras + nuvem de pontos esparsa)
      -> OpenSplat   (treino do Gaussian Splat, CUDA)
      -> visualizador web (.ply no browser)

Uso rapido:
  python splat.py run video.mp4
  python splat.py run C:\\fotos\\estatua --iters 15000
  python splat.py view work\\video\\output\\splat.ply

Apenas biblioteca padrao do Python (3.9+).
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS_DIR = ROOT / "tools"
VIEWER_DIR = ROOT / "viewer"
IS_WINDOWS = os.name == "nt"

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm", ".mts", ".wmv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


# --------------------------------------------------------------------------
# Utilitarios
# --------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"\n\033[1;36m==> {msg}\033[0m", flush=True)


def fail(msg: str) -> None:
    print(f"\n\033[1;31mERRO:\033[0m {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list, **kw) -> None:
    printable = " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd)
    print(f"$ {printable}", flush=True)
    t0 = time.time()
    res = subprocess.run([str(c) for c in cmd], **kw)
    if res.returncode != 0:
        fail(f"o comando terminou com codigo {res.returncode}: {cmd[0]}")
    print(f"   ({time.time() - t0:.0f}s)")


def capture(cmd: list) -> str:
    try:
        res = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                             errors="replace")
        return (res.stdout or "") + (res.stderr or "")
    except OSError:
        return ""


def find_tool(names: list[str], env_var: str) -> Path | None:
    """Procura um executavel: variavel de ambiente -> pasta tools/ -> PATH."""
    env = os.environ.get(env_var)
    if env and Path(env).is_file():
        return Path(env)
    if TOOLS_DIR.is_dir():
        for name in names:
            hits = sorted(TOOLS_DIR.rglob(name), key=lambda p: len(p.parts))
            hits = [h for h in hits if h.is_file()]
            if hits:
                return hits[0]
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


@functools.lru_cache(maxsize=None)
def tool(kind: str) -> Path:
    table = {
        # COLMAP.bat (Windows) configura o PATH das DLLs; preferir esse.
        "colmap": (["COLMAP.bat", "colmap.exe", "colmap"], "SPLAT_COLMAP"),
        "ffmpeg": (["ffmpeg.exe", "ffmpeg"], "SPLAT_FFMPEG"),
        "ffprobe": (["ffprobe.exe", "ffprobe"], "SPLAT_FFPROBE"),
        "opensplat": (["opensplat.exe", "opensplat"], "SPLAT_OPENSPLAT"),
    }
    names, env = table[kind]
    p = find_tool(names, env)
    if not p:
        fail(f"nao encontrei '{kind}'. Corre setup\\install-tools.ps1 "
             f"(e setup\\build-opensplat.ps1 para o OpenSplat), "
             f"ou define a variavel {env} com o caminho do executavel.")
    return p


# --------------------------------------------------------------------------
# Passo 1: frames
# --------------------------------------------------------------------------

def video_duration(video: Path) -> float | None:
    out = capture([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                   "-of", "default=noprint_wrappers=1:nokey=1", video])
    try:
        return float(out.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def step_frames(src: Path, ws: Path, args) -> str:
    """Prepara ws/images. Devolve 'video' ou 'photos' (define o tipo de matching)."""
    images = ws / "images"
    if images.is_dir() and any(images.iterdir()) and not args.force:
        print(f"   {images} ja existe - a reutilizar (usa --force para refazer)")
        return "video" if src.is_file() else "photos"
    if images.exists():
        shutil.rmtree(images)
    images.mkdir(parents=True)

    if src.is_dir():
        log("A copiar fotografias")
        photos = sorted(p for p in src.iterdir() if p.suffix.lower() in IMAGE_EXTS)
        if len(photos) < 3:
            fail(f"preciso de pelo menos 3 fotografias em {src} (idealmente 30-200).")
        for i, p in enumerate(photos):
            # Redimensionar com ffmpeg mantem tudo sem dependencias extra.
            dst = images / f"{i:05d}.jpg"
            run([tool("ffmpeg"), "-loglevel", "error", "-y", "-i", p,
                 "-vf", f"scale='min({args.max_size},iw)':'min({args.max_size},ih)'"
                        f":force_original_aspect_ratio=decrease",
                 "-q:v", "2", dst])
        print(f"   {len(photos)} fotografias preparadas")
        return "photos"

    if src.suffix.lower() not in VIDEO_EXTS:
        fail(f"formato nao suportado: {src.suffix}. Usa um video ou uma pasta de fotos.")

    log("A extrair frames do video")
    fps = args.fps
    if fps is None:
        dur = video_duration(src)
        if not dur:
            fps = 2.0
            print("   nao consegui ler a duracao; a usar 2 fps")
        else:
            fps = max(0.2, min(args.frames / dur, 30.0))
            print(f"   duracao {dur:.1f}s -> {fps:.2f} fps para ~{args.frames} frames")
    vf = (f"fps={fps},scale='min({args.max_size},iw)':'min({args.max_size},ih)'"
          f":force_original_aspect_ratio=decrease")
    run([tool("ffmpeg"), "-loglevel", "error", "-stats", "-y", "-i", src,
         "-vf", vf, "-q:v", "2", images / "%05d.jpg"])
    n = len(list(images.glob("*.jpg")))
    if n < 3:
        fail("o video produziu menos de 3 frames. Aumenta --fps ou --frames.")
    print(f"   {n} frames extraidos")
    return "video"


# --------------------------------------------------------------------------
# Passo 2: COLMAP
# --------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def colmap_help(command: str) -> str:
    return capture([tool("colmap"), command, "-h"])


def colmap_flag(command: str, candidates: list[str]) -> str | None:
    """As flags mudaram de nome entre versoes do COLMAP (ex.: SiftExtraction.use_gpu
    -> FeatureExtraction.use_gpu). Escolhe a que existir nesta versao."""
    text = colmap_help(command)
    for c in candidates:
        if f"--{c}" in text:
            return f"--{c}"
    return None


def pick_best_model(sparse: Path) -> Path:
    """O mapper pode criar varios modelos (0, 1, ...). Escolhe o com mais imagens."""
    models = [d for d in sparse.iterdir() if d.is_dir() and (d / "images.bin").exists()]
    if not models:
        fail("o COLMAP nao conseguiu reconstruir a cena. Dicas: mais sobreposicao entre "
             "frames, movimento mais lento, menos desfocagem, superficies com textura.")
    best = max(models, key=lambda d: (d / "images.bin").stat().st_size)
    if len(models) > 1:
        print(f"   {len(models)} modelos encontrados; a usar {best.name} (o maior)")
    return best


def step_colmap(ws: Path, mode: str, args) -> Path:
    dataset = ws / "dataset"
    if (dataset / "sparse" / "0" / "cameras.bin").exists() and not args.force:
        print(f"   {dataset} ja existe - a reutilizar (usa --force para refazer)")
        return dataset

    db = ws / "database.db"
    sparse = ws / "sparse"
    for p in (db, sparse, dataset):
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    sparse.mkdir()
    gpu = "0" if args.cpu else "1"

    log("COLMAP 1/4: extracao de features")
    cmd = [tool("colmap"), "feature_extractor",
           "--database_path", db, "--image_path", ws / "images",
           "--ImageReader.camera_model", args.camera_model,
           "--ImageReader.single_camera", "1" if mode == "video" else "0"]
    flag = colmap_flag("feature_extractor",
                       ["FeatureExtraction.use_gpu", "SiftExtraction.use_gpu"])
    if flag:
        cmd += [flag, gpu]
    run(cmd)

    matcher = args.matcher or ("sequential" if mode == "video" else "exhaustive")
    log(f"COLMAP 2/4: matching ({matcher})")
    command = f"{matcher}_matcher"
    cmd = [tool("colmap"), command, "--database_path", db]
    flag = colmap_flag(command, ["FeatureMatching.use_gpu", "SiftMatching.use_gpu"])
    if flag:
        cmd += [flag, gpu]
    if matcher == "sequential":
        ov = colmap_flag(command, ["SequentialMatching.overlap"])
        if ov:
            cmd += [ov, "15"]
    run(cmd)

    log("COLMAP 3/4: reconstrucao (mapper) - pode demorar")
    run([tool("colmap"), "mapper", "--database_path", db,
         "--image_path", ws / "images", "--output_path", sparse])
    model = pick_best_model(sparse)

    log("COLMAP 4/4: remover distorcao das imagens")
    run([tool("colmap"), "image_undistorter", "--image_path", ws / "images",
         "--input_path", model, "--output_path", dataset,
         "--output_type", "COLMAP", "--max_image_size", str(args.max_size)])

    # O undistorter escreve dataset/sparse/*.bin; o layout canonico e sparse/0/.
    sp = dataset / "sparse"
    (sp / "0").mkdir(exist_ok=True)
    for f in sp.glob("*.bin"):
        shutil.move(str(f), str(sp / "0" / f.name))
    n = len(list((dataset / "images").iterdir()))
    print(f"   dataset pronto: {n} imagens registadas em {dataset}")
    return dataset


# --------------------------------------------------------------------------
# Passo 3: OpenSplat
# --------------------------------------------------------------------------

def step_train(dataset: Path, ws: Path, args) -> Path:
    out_dir = ws / "output"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / "splat.ply"
    log(f"OpenSplat: treino ({args.iters} iteracoes)")
    cmd = [tool("opensplat"), dataset.resolve(), "-n", str(args.iters), "-o", out.resolve()]
    if args.cpu:
        cmd += ["--cpu"]
    if args.opensplat_args:
        cmd += args.opensplat_args.split()
    # O OpenSplat precisa das DLLs do libtorch/OpenCV ao lado do .exe.
    run(cmd, cwd=str(tool("opensplat").parent))
    if not out.exists():
        fail(f"o OpenSplat terminou mas nao encontrei {out}")
    print(f"   splat gravado: {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return out


# --------------------------------------------------------------------------
# Passo 4: visualizador
# --------------------------------------------------------------------------

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def step_view(ply: Path | None, port: int, open_browser: bool = True) -> None:
    serve = ROOT
    url = f"http://localhost:{port}/viewer/index.html"
    if ply:
        ply = ply.resolve()
        try:
            rel = ply.relative_to(serve)
        except ValueError:
            # Fora da pasta do projeto: copiar para work/_view/.
            dst = ROOT / "work" / "_view" / ply.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ply, dst)
            rel = dst.relative_to(serve)
        url += "?file=/" + rel.as_posix()

    handler = functools.partial(QuietHandler, directory=str(serve))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    log(f"Visualizador em {url}")
    print("   Ctrl+C para terminar.")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def workspace_for(src: Path, args) -> Path:
    if args.workspace:
        return Path(args.workspace).resolve()
    name = src.stem if src.is_file() else src.name
    return (ROOT / "work" / name).resolve()


def cmd_run(args) -> None:
    src = Path(args.input).resolve()
    if not src.exists():
        fail(f"nao existe: {src}")
    ws = workspace_for(src, args)
    ws.mkdir(parents=True, exist_ok=True)
    print(f"Workspace: {ws}")
    t0 = time.time()
    mode = step_frames(src, ws, args)
    dataset = step_colmap(ws, mode, args)
    ply = step_train(dataset, ws, args)
    log(f"Concluido em {(time.time() - t0) / 60:.1f} min -> {ply}")
    json.dump({"input": str(src), "ply": str(ply), "iters": args.iters},
              open(ws / "splat.json", "w"), indent=2)
    if not args.no_view:
        step_view(ply, args.port)


def cmd_frames(args) -> None:
    src = Path(args.input).resolve()
    ws = workspace_for(src, args)
    ws.mkdir(parents=True, exist_ok=True)
    step_frames(src, ws, args)


def cmd_colmap(args) -> None:
    ws = Path(args.workspace).resolve()
    if not (ws / "images").is_dir():
        fail(f"{ws / 'images'} nao existe. Corre primeiro 'frames'.")
    step_colmap(ws, args.mode, args)


def cmd_train(args) -> None:
    ws = Path(args.workspace).resolve()
    dataset = ws / "dataset"
    if not (dataset / "sparse").is_dir():
        fail(f"{dataset} nao tem reconstrucao COLMAP. Corre primeiro 'colmap'.")
    step_train(dataset, ws, args)


def cmd_view(args) -> None:
    ply = Path(args.ply) if args.ply else None
    if ply and not ply.exists():
        fail(f"nao existe: {ply}")
    step_view(ply, args.port)


def cmd_check(args) -> None:
    log("Ferramentas")
    ok = True
    for kind in ("ffmpeg", "ffprobe", "colmap", "opensplat"):
        names = {"colmap": ["COLMAP.bat", "colmap.exe", "colmap"],
                 "opensplat": ["opensplat.exe", "opensplat"]}.get(kind, [f"{kind}.exe", kind])
        p = find_tool(names, f"SPLAT_{kind.upper()}")
        print(f"   {kind:10s} {'OK  ' + str(p) if p else 'EM FALTA'}")
        ok &= p is not None
    nv = shutil.which("nvidia-smi")
    if nv:
        out = capture([nv, "--query-gpu=name,memory.total,driver_version",
                       "--format=csv,noheader"])
        print(f"   GPU        {out.strip()}")
    else:
        print("   GPU        nvidia-smi nao encontrado")
    if not ok:
        print("\nCorre setup\\install-tools.ps1 e setup\\build-opensplat.ps1.")
        sys.exit(1)


def add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--workspace", "-w", help="pasta de trabalho (default: work/<nome>)")
    p.add_argument("--force", action="store_true", help="refazer passos ja feitos")
    p.add_argument("--max-size", type=int, default=1600,
                   help="lado maximo das imagens em px (default 1600)")


def main() -> None:
    if IS_WINDOWS:
        os.system("")  # ativa cores ANSI na consola do Windows

    ap = argparse.ArgumentParser(prog="splat", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="pipeline completo: frames -> COLMAP -> OpenSplat -> viewer")
    p.add_argument("input", help="ficheiro de video ou pasta de fotografias")
    add_common(p)
    p.add_argument("--frames", type=int, default=150,
                   help="n.o alvo de frames a extrair do video (default 150)")
    p.add_argument("--fps", type=float, help="fps fixo (ignora --frames)")
    p.add_argument("--iters", "-n", type=int, default=7000,
                   help="iteracoes de treino (7000 rapido, 30000 qualidade max)")
    p.add_argument("--matcher", choices=["sequential", "exhaustive"],
                   help="default: sequential para video, exhaustive para fotos")
    p.add_argument("--camera-model", default="OPENCV",
                   help="modelo de camara COLMAP (default OPENCV)")
    p.add_argument("--cpu", action="store_true", help="nao usar GPU (muito lento)")
    p.add_argument("--opensplat-args", help='argumentos extra, ex: "--downscale-factor 2"')
    p.add_argument("--no-view", action="store_true", help="nao abrir o visualizador no fim")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("frames", help="so extrair frames / preparar fotos")
    p.add_argument("input")
    add_common(p)
    p.add_argument("--frames", type=int, default=150)
    p.add_argument("--fps", type=float)
    p.set_defaults(func=cmd_frames)

    p = sub.add_parser("colmap", help="so correr o COLMAP numa workspace")
    p.add_argument("workspace")
    p.add_argument("--mode", choices=["video", "photos"], default="video")
    p.add_argument("--force", action="store_true")
    p.add_argument("--max-size", type=int, default=1600)
    p.add_argument("--matcher", choices=["sequential", "exhaustive"])
    p.add_argument("--camera-model", default="OPENCV")
    p.add_argument("--cpu", action="store_true")
    p.set_defaults(func=cmd_colmap)

    p = sub.add_parser("train", help="so treinar o OpenSplat numa workspace")
    p.add_argument("workspace")
    p.add_argument("--iters", "-n", type=int, default=7000)
    p.add_argument("--cpu", action="store_true")
    p.add_argument("--opensplat-args")
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("view", help="abrir um .ply no visualizador web")
    p.add_argument("ply", nargs="?", help="ficheiro .ply / .splat (opcional)")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_view)

    p = sub.add_parser("check", help="verificar se as ferramentas estao instaladas")
    p.set_defaults(func=cmd_check)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
