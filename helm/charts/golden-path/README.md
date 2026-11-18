# golden-path chart

Example workload chart for Deployground. It is published as a versioned OCI Helm chart at
**`oci://ghcr.io/rhysmcneill/golden-path`**, tagged to match each Deployground release (see
`.github/workflows/release-please.yml`).

By default, both deploy targets (`applicationset` and `application`) pull this chart straight
from that OCI registry — pinned to the running bot's own version unless
`deployTarget.workloadChart.version` overrides it. **No manual copy into the target GitOps
repo is required.** The generated Argo CD manifests use a multi-source `Application` /
`ApplicationSet` template: one source is the chart (OCI by default, or your own chart via
`deployTarget.workloadChart.type: git`), the other supplies the per-component values from the
target repo.

To use your own chart instead of the published default, set `deployTarget.workloadChart` in
your Deployground config, e.g.:

```yaml
deployTarget:
  workloadChart:
    type: git
    gitRepo: https://github.com/my-org/my-charts.git
    gitRef: main
    chartPath: charts/my-workload
```

It consumes the workload keys of one component file: `image`, `port`, `replicas`,
`ingressHost`, `env` (map), `secretRefs` (list of Secret names, injected via `envFrom`),
`imagePullSecrets` (list of pre-existing `kubernetes.io/dockerconfigjson` Secret names, wired
onto the pod spec so kubelet can pull from a private registry) and `serviceAccountName` (a
pre-existing ServiceAccount, e.g. annotated for IRSA, that the pod runs under so the
workload's own code can call AWS APIs at runtime — this does NOT help pull private images,
since kubelet pulls the image before any pod-runtime mechanism like IRSA is available; for
ECR specifically there are also node-level and image-credential-provider mechanisms outside
this chart's scope). The `deployground:` metadata block in the file is ignored. Renders a
Deployment, a Service (when `port` is set) and an Ingress (when `port` and `ingressHost` are
set).

Resource requests/limits come from this chart's `values.yaml` defaults, not from component files.
Edit them there to change the platform-wide defaults.

Deployground also renders one reserved pseudo-component per stack (minimum chart version
0.5.0) whose Application sets `namespaceOnly: true` and no other values. A chart used via
`deployTarget.workloadChart.type: git` (or pinned to an older OCI version) must handle this
key by rendering ONLY a labelled `Namespace` object (`metadata.labels`:
`deployground.io/managed: "true"`) and nothing else — see `templates/namespace.yaml` for the
reference implementation, including the ownership guard that skips rendering when the
namespace already exists and isn't labelled `deployground.io/managed`. A custom chart that
doesn't handle `namespaceOnly` will fail to sync for that pseudo-component (e.g. by trying to
render a Deployment with no `image`).

Try it locally:

    helm template pool-party-web helm/charts/golden-path -f path/to/component/config.yaml
