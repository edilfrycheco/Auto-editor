# Auto Editor (V3)

Un Workflow Integration para DaVinci Resolve Studio que automatiza el corte de retakes, saltos y silencios basado en guion.

## Entornos Conda (Aislamiento Crítico)

**IMPORTANTÍSIMO:** Auto B-Roll y Auto Editor son dos herramientas independientes. Para evitar que las librerías modernas de Auto Editor (como WhisperX que requiere PyTorch 2.x+) rompan las librerías antiguas que requiere Auto B-Roll, se DEBEN mantener aislados mediante entornos Conda.

### Entorno Auto Editor
Usa el entorno `autoeditor`:
```bash
conda create -n autoeditor python=3.11 -y
conda activate autoeditor
pip install -r requirements.txt
```

El launcher del Auto Editor (`Auto_Editor.py` en DaVinci Resolve Workflow Integration Plugins) apunta explícitamente a: `/opt/anaconda3/envs/autoeditor/bin/python3`

### Entorno Auto B-Roll (Existente)
El Auto B-Roll actual (Standlone App) vive en el entorno `base` de Anaconda (o si se aísla a futuro, en un entorno `autobroll`). Su launcher (`Auto_B_Roll.py`) apunta a `/opt/anaconda3/bin/python3` (base) y **NO DEBE TOCARSE**.
