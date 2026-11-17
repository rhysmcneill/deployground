"""Resolves `deployTarget.workloadChart` into an Argo CD multi-source `source` entry.

Shared by both deploy targets so the chart source (OCI by default, or an operator's own
git chart) is computed the same way regardless of whether the component's values are
templated (ApplicationSet) or literal (Application).
"""

from typing import Any

from deployground import __version__
from deployground.config.models import WorkloadChartCfg


def chart_source(cfg: WorkloadChartCfg) -> dict[str, Any]:
    """The chart `source` entry (repoURL/targetRevision[/path]), with no `helm` key yet —
    callers attach `helm.valueFiles` (or `helm.values`) once they know the values source."""
    if cfg.type == "git":
        return {
            "repoURL": cfg.gitRepo,
            "targetRevision": cfg.gitRef,
            "path": cfg.chartPath,
        }
    repo = cfg.repo or "ghcr.io/rhysmcneill"
    chart = cfg.chart or "golden-path"
    version = cfg.version or __version__
    return {
        "repoURL": f"oci://{repo}/{chart}",
        "targetRevision": version,
    }
