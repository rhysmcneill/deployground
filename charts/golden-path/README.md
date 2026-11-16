# golden-path chart

Example workload chart for Deployground. The generated ApplicationSet deploys every component
file with `source.path: golden-path` and passes the file as a Helm value file, so:

**This chart must live at `golden-path/` (repo root) in the target GitOps repo** (the repo named
by `deployTarget.repo`). Copy this directory there as `golden-path/`.

It consumes the workload keys of one component file: `image`, `port`, `replicas`,
`ingressHost`, `env` (map), `secretRefs` (list of Secret names, injected via `envFrom`).
The `deployground:` metadata block in the file is ignored. Renders a Deployment, a Service (when
`port` is set) and an Ingress (when `port` and `ingressHost` are set).

Resource requests/limits come from this chart's `values.yaml` defaults, not from component files.
Edit them there to change the platform-wide defaults.

Try it locally:

    helm template pool-party-web charts/golden-path -f path/to/component/config.yaml
