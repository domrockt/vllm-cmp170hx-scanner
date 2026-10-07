# vLLM Observability Dashboard

Portable local dashboard for vLLM, NVIDIA GPUs, host hardware, benchmarks, and optional CMP 170HX fuse readouts.

## Security

- No credentials, private hosts, or SSH targets are included.
- The server discovers local Docker containers, NVIDIA GPUs, and hardware automatically.
- The fuse readout only runs on the local machine and only when explicitly requested.

## Run

```bash
python3 app.py
```

Then open `http://127.0.0.1:8080/`.

The dashboard searches local vLLM containers and their published ports. GPU and system data are read from local `nvidia-smi`, sysfs, and DMI. Missing data is shown as unavailable rather than guessed.
