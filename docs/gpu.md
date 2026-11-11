# Running the model on a GPU

The reference server has no GPU, so every number in this project was measured on CPU.
This page lists what it takes to give the Ollama container a GPU on Fedora, in case you run the project on
a machine that has one. Nothing else in the project changes: the API talks to Ollama the same way.

## What was checked on the reference server

| Check | Result |
|---|---|
| `nvidia-smi` | not installed |
| `lspci` | only the QEMU virtual VGA adapter (1234:1111) |
| `/dev/nvidia*`, `/dev/kfd` | absent |
| CPU flags | SSE4.2 only (the VM uses the generic QEMU CPU model, so AVX and AVX2 are hidden) |

Two things would speed it up without a GPU:

1. **Expose the host CPU to the VM.** In Proxmox set Processor type to `host`; in virt-manager or libvirt use
   `host-passthrough`. The model then gets AVX2 and FMA, which llama.cpp uses heavily.
2. **Keep `num_batch` at 64.** On this CPU it keeps the prompt cache 64 tokens from the end and cut the time per
   comment from 28 s to 6 s (see `config/settings.yaml`).

## NVIDIA

Requirements from Ollama: compute capability 5.0 or newer and driver 550 or newer.

1. Driver: install the proprietary driver from RPM Fusion (`akmod-nvidia`), reboot, and confirm with `nvidia-smi`.
2. Container toolkit: add NVIDIA's repository
   (`https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo`) and install
   `nvidia-container-toolkit`.
3. Tell Docker about it, either way works:
   * runtime hook: `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`
   * CDI: `sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml` (Docker already reads `/etc/cdi`)
4. Give the `ollama` service the GPU in `docker-compose.yml`:

   ```yaml
   ollama:
     gpus: all                      # Compose 2.30 or newer
     # or, with CDI:
     # devices: ["nvidia.com/gpu=all"]
   ```

5. Check: `docker compose logs ollama` should list a CUDA device under "inference compute".

## AMD

1. Use the ROCm image: `ollama/ollama:0.40.16-rocm`.
2. Pass the devices and groups to the `ollama` service:

   ```yaml
   ollama:
     image: ollama/ollama:0.40.16-rocm
     devices: ["/dev/kfd", "/dev/dri"]
     group_add: ["video", "render"]
   ```

3. SELinux can block containers from opening GPU devices: `sudo setsebool -P container_use_devices=1`.

## SELinux, Docker and Podman

* The reference server runs Docker without SELinux labeling, so bind mounts and devices need no extra flags.
* With Podman, or with Docker configured for SELinux labeling, add `:z` to bind mounts and either enable
  `container_use_devices` or run the GPU container with `--security-opt label=disable`.
* Rootless Podman with NVIDIA works through CDI: `podman run --device nvidia.com/gpu=all ...`.

## Inside a virtual machine

A VM only sees a GPU that the hypervisor passes through (VFIO). On Proxmox: enable IOMMU on the host, add the GPU
as a PCI device to the VM, then follow the steps above inside the VM.

## Sources

* Ollama hardware support: https://docs.ollama.com/gpu
* Ollama Docker: https://docs.ollama.com/docker
* NVIDIA Container Toolkit install guide: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
* Docker Compose GPU access: https://docs.docker.com/compose/how-tos/gpu-support/
