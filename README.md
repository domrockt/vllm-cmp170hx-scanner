# vLLM Observability

[Buy me a coffee](https://paypal.me/Domrockt)

Self-discovering dashboard for vLLM, NVIDIA GPUs and host hardware. It binds to `0.0.0.0:8080`, so every computer in the local network can open it.

Languages: Deutsch, English, Español, Français, 中文.


## Windows

No Python install required. Download or clone the repo and run:

```bat
dist\vllm-observability-windows-x64.exe
```

Then open `http://SERVER-IP:8080`. Windows Firewall may ask for permission the first time. Allow it only on private networks if you want other PCs on the LAN to open the dashboard.

The file is a 64-bit Windows console EXE built with Windows Python 3.11. It listens on `0.0.0.0:8080` by default. Another port:

```bat
set PORT=8090
dist\vllm-observability-windows-x64.exe
```

## Where to install

Install the dashboard on the same computer that runs vLLM and the CMP 170HX. GPU, RAM, NVMe and fuse readout are local hardware reads and are complete only there.

If vLLM runs on another computer, `./install.sh` asks for its host or IP and saves it in `.env` as `VLLM_HOST`. Hardware details may then be limited, but vLLM metrics remain available.

## Install

```bash
git clone https://github.com/domrockt/vllm-cmp170hx-scanner.git
cd vllm-cmp170hx-scanner
./install.sh
```

Open `http://SERVER-IP:8080`.

## For AI agents

1. Clone this repository.
2. Run `./install.sh`; Python 3.9+ is the only requirement.
3. Do not add credentials or edit host addresses.
4. The app discovers local vLLM endpoints, GPUs and hardware itself.
5. Verify `http://127.0.0.1:8080/api/state` returns JSON.



## What the dashboard does

The dashboard is read-only except for the benchmark, which sends test requests only after confirmation.

- vLLM status: finds local OpenAI-compatible endpoints and reads model, context length, running requests, waiting requests, KV cache, latency and token throughput from `/v1/models` and Prometheus `/metrics`.
- GPU status: reads NVIDIA GPU name, memory, temperature, power, clocks and compute processes with `nvidia-smi`.
- CMP 170HX readout: shows active SMs and a read-only fuse comparison. It reads GPU BAR0 with Python `mmap` and compares disable masks with defective masks. It never writes GPU registers.
- Host hardware: reads mainboard, CPU, RAM, NVMe and the PCIe switch through `dmidecode`, `/proc`, `/sys` and `lspci`.
- Benchmark: runs confirmed concurrency levels C1, C2, C4, C8 and C16 against the selected endpoint. Each request asks for 2048 completion tokens. Results show aggregate tok/s and stay in a local history.
- History: stores the last 100 runs in `benchmark-history.json`, grouped by model, and can delete them from the dashboard.
- Languages: Deutsch, English, Español, Français and 中文. The browser stores the choice.

## Tools used

| Function | Tool or source | Access |
|---|---|---|
| vLLM discovery | `/v1/models` | read |
| live metrics | Prometheus `/metrics` | read |
| GPU telemetry | `nvidia-smi` | read |
| CMP fuse readout | Python `mmap` on GPU BAR0 | read-only, root required |
| host hardware | `dmidecode`, `/proc`, `/sys`, `lspci` | read |
| benchmark | OpenAI-compatible `/v1/chat/completions` | confirmed test requests |
| result history | local `benchmark-history.json` | local file |
| dashboard server | Python standard-library HTTP server | LAN port 8080 |

No database, account, API key or cloud service is required.

## Security

The benchmark sends load to a selected vLLM endpoint and requires confirmation. The optional GPU fuse readout is read-only and needs root access to GPU registers. Do not expose port 8080 directly to the public internet without an access proxy.
