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
    chart = cfg.chart or "golden-path-chart"
    version = cfg.version or __version__
    # Argo CD's Helm-OCI source needs the chart name as its own `chart` field, and in that
    # form repoURL is the bare registry host -- no oci:// scheme. (The oci:// form is only
    # for baking the chart name directly into repoURL, which leaves `chart` unset and Argo
    # CD rejects the source outright.) Matches the known-working convention already used
    # for deployground's own app source: repoURL "ghcr.io/rhysmcneill", chart
    # "deployground-chart".
    return {
        "repoURL": repo,
        "chart": chart,
        "targetRevision": version,
    }


def chart_fields(cfg: WorkloadChartCfg) -> dict[str, str]:
    """Flattens a WorkloadChartCfg into all four possible chart-source keys, always present
    (empty string for whichever pair `cfg.type` doesn't use) -- for callers that must
    substitute one static template across many per-file chart refs, where every key must
    exist in every file or ArgoCD's generator leaves the literal, un-substituted placeholder
    in place instead of an empty string."""
    if cfg.type == "git":
        return {
            "repoURL": cfg.gitRepo or "",
            "targetRevision": cfg.gitRef or "",
            "path": cfg.chartPath or "",
            "chart": "",
        }
    return {
        "repoURL": cfg.repo or "ghcr.io/rhysmcneill",
        "chart": cfg.chart or "golden-path-chart",
        "targetRevision": cfg.version or __version__,
        "path": "",
    }
