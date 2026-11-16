"""Status sources. ``make_status_source`` is the factory selecting a source by ``cfg.type``."""

from deployground.config.models import StatusSourceCfg
from deployground.status.argocd_api import ArgoApiSource
from deployground.status.base import StatusSource
from deployground.status.notifications import NotificationsSource


def make_status_source(cfg: StatusSourceCfg) -> StatusSource:
    if cfg.type == "argocd-notifications":
        return NotificationsSource(cfg)
    if cfg.type == "argocd-api":
        return ArgoApiSource(cfg)
    raise ValueError(f"unknown status source type: {cfg.type!r}")
