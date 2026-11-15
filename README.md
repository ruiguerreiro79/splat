# Splat

Gera um **Gaussian Splat** (`.ply`) a partir de um **vídeo** ou de uma **pasta de fotografias**, num PC Windows com GPU NVIDIA.

```
vídeo / fotos ──ffmpeg──▶ frames ──COLMAP──▶ poses + pontos ──OpenSplat (CUDA)──▶ splat.ply ──▶ visualizador web
```

Projetos usados:
- [COLMAP](https://github.com/colmap/colmap): Structure-from-Motion (posições das câmaras)
- [OpenSplat](https://github.com/pierotofy/OpenSplat): treino de 3D Gaussian Splatting (CUDA)
- [GaussianSplats3D](https://github.com/mkkellogg/GaussianSplats3D): visualizador no browser
- [ffmpeg](https://ffmpeg.org): extração de frames

## Instalação (uma vez)

1. Instala os pré-requisitos:
   ```powershell
   winget install Python.Python.3.12 Git.Git Kitware.CMake
   winget install Microsoft.VisualStudio.2022.Community   # depois, no Installer: "Desktop development with C++"
   ```
   E ainda o **CUDA Toolkit 11.8** ([download](https://developer.nvidia.com/cuda-11-8-0-download-archive)).
   Se tiveres uma **RTX 50xx**, instala o CUDA 12.8 (ver passo 3).

2. Instala o ffmpeg e o COLMAP (para `tools\`):
   ```powershell
   powershell -ExecutionPolicy Bypass -File setup\install-tools.ps1
   ```

3. Compila o OpenSplat (descarrega o libtorch, ~2,5 GB, e o OpenCV):
   ```powershell
   powershell -ExecutionPolicy Bypass -File setup\build-opensplat.ps1
   # RTX 50xx:
   powershell -ExecutionPolicy Bypass -File setup\build-opensplat.ps1 -CudaTag cu128 -TorchVersion 2.7.1 -CudaArch 120
   ```

4. Verifica:
   ```powershell
   python splat.py check
   ```

## Utilização

```powershell
python splat.py run C:\videos\estatua.mp4               # pipeline completo + abre o visualizador
python splat.py run C:\fotos\sala --iters 30000         # pasta de fotos, qualidade máxima
python splat.py view work\estatua\output\splat.ply      # voltar a ver um resultado
python splat.py view                                    # visualizador vazio (arrastar ficheiros)
```

Os resultados ficam em `work\<nome>\`:

| Pasta | Conteúdo |
|---|---|
| `images\` | frames extraídos |
| `sparse\`, `database.db` | reconstrução COLMAP |
| `dataset\` | imagens sem distorção + `sparse\0`, entrada do OpenSplat |
| `output\splat.ply` | **o resultado** |

Os passos já feitos são reaproveitados (usa `--force` para os refazer). Também os podes correr à parte: `frames`, `colmap`, `train`.

### Opções úteis

| Opção | Default | Para quê |
|---|---|---|
| `--frames N` | 150 | n.º de frames a tirar do vídeo |
| `--fps X` | – | fps fixo em vez de `--frames` |
| `--iters N` | 7000 | iterações de treino (30000 = qualidade máxima) |
| `--max-size PX` | 1600 | resolução máxima das imagens (baixa se faltar VRAM) |
| `--matcher` | auto | `sequential` (vídeo) ou `exhaustive` (fotos) |
| `--opensplat-args "..."` | – | argumentos extra para o OpenSplat (`opensplat --help`) |

## Dicas para bons resultados

- Anda **à volta** do objeto devagar e mantém a câmara estável; evita desfocagem.
- Ilumina de forma uniforme e não deixes nada a mexer-se na cena.
- Superfícies lisas, brilhantes ou transparentes falham no COLMAP.
- Para fotos: 50 a 200, com ~70% de sobreposição entre fotos seguidas.
- Uma **única imagem** não chega: o COLMAP precisa de várias vistas.

## Problemas comuns

- **"o COLMAP nao conseguiu reconstruir a cena"**: há pouca sobreposição. Aumenta `--frames` ou experimenta `--matcher exhaustive`.
- **Falta de memória na GPU**: usa `--max-size 1200` ou `--opensplat-args "--downscale-factor 2"`.
- **Erro de DLL ao arrancar o OpenSplat**: volta a correr `build-opensplat.ps1`, que copia as DLLs para `tools\opensplat\`.
- **Ferramentas noutro sítio**: define `SPLAT_COLMAP`, `SPLAT_FFMPEG`, `SPLAT_FFPROBE` ou `SPLAT_OPENSPLAT` com o caminho do executável.
