---
title: RevelaCode Legend Child
emoji: 🧠
colorFrom: green
colorTo: blue
sdk: docker
app_port: 8000
---

# RevelaCode Legend Child

This Space runs one independent Legend child container.

Set child-specific environment variables through Space secrets. Do not put API keys or HMAC secrets in this file or repository.

The Docker build context must be the repository root so the root Dockerfile can package the Python engine and T-Lang seed library.
