# Child deployment notes

Legend children are API deployments with their own local verified T-Lang cache. The Docker image is portable; each child receives a unique ID and its own secrets.

## Build and run

    docker build -t revelacode-legend:latest .
    docker run --rm -p 8000:8000 \
      -e LEGEND_CHILD_ID=legend-child-a \
      -e LEGEND_CHILD_API_KEY=replace-with-random-secret \
      -e LEGEND_ADMIN_API_KEY=replace-with-separate-secret \
      -e LEGEND_GITHUB_TOKEN=github-fine-grained-token \
      -e LEGEND_TEACHER_API_KEY=teacher-api-key \
      -e LEGEND_TEACHER_MODEL=teacher-model-id \
      -v legend-data:/app/data \
      revelacode-legend:latest

Check GET /v1/health. Never place any API key in the Vite frontend.

## Hosting layout

- Render: the root render.yaml blueprint defines two independent child services with separate child IDs and independently entered secrets.
- Hugging Face: create a Docker Space from this repository's Dockerfile (or mirror the container build context to a Space). Set the same environment variables in Space secrets.
- AWS: build and publish this Dockerfile as an OCI image, then run it in an AWS container service. Set secrets in the hosting platform, not in the image.

Each child should use a unique LEGEND_CHILD_ID and separate LEGEND_CHILD_API_KEY. Give LEGEND_GITHUB_TOKEN only repository Contents and Pull requests write access to this Home repo. Teacher credentials are optional. Without them, the child can still compose verified templates and accept ChatGPT-generated T-Lang manually through /v1/learn/import.

## Frontend connection

Set VITE_LEGEND_API_BASE in the Netlify build environment to the deployed child API origin, for example https://your-child-service.example.com. Set LEGEND_CORS_ORIGINS on the API to the frontend origin. Do not set secrets as VITE_* variables.

## Persistence and scale

Mount persistent storage at /app/data for SQLite. If the service uses ephemeral storage, its local cache is rebuilt from Home on restart; Home remains the persistent library. For multiple replicas, use a shared transactional database rather than a container-local SQLite file. Keep child APIs isolated from Home write credentials where possible; only the learning/submission worker needs Contents + Pull requests write access.
