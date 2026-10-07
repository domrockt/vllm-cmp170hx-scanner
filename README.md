# vLLM Observability

[Buy me a coffee](https://paypal.me/Domrockt)

Self-discovering dashboard for vLLM, NVIDIA GPUs and host hardware. It binds to `0.0.0.0:8080`, so every computer in the local network can open it.

Languages: Deutsch, English, Español, Français, 中文.

## Install

```bash
git clone https://github.com/domrockt/vllm-observability.git
cd vllm-observability
./install.sh
```

Open `http://SERVER-IP:8080`.

## For AI agents

1. Clone this repository.
2. Run `./install.sh`; Python 3.9+ is the only requirement.
3. Do not add credentials or edit host addresses.
4. The app discovers local vLLM endpoints, GPUs and hardware itself.
5. Verify `http://127.0.0.1:8080/api/state` returns JSON.

## Security

The benchmark sends load to a selected vLLM endpoint and requires confirmation. The optional GPU fuse readout is read-only and needs root access to GPU registers. Do not expose port 8080 directly to the public internet without an access proxy.
