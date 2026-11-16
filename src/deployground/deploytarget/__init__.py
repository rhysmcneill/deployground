"""Deploy targets and the `make_deploy_target` factory (the composition root's entry point)."""

from deployground.config.models import DeployTargetCfg, RepoRef
from deployground.deploytarget.application import ApplicationTarget
from deployground.deploytarget.applicationset import ApplicationSetTarget
from deployground.deploytarget.base import DeployTarget


def make_deploy_target(
    cfg: DeployTargetCfg, repo_ref: RepoRef, env_clusters: dict[str, str]
) -> DeployTarget:
    """Select the target by `cfg.type`. `env_clusters` (env name -> cluster) is only used
    by the Application target, which writes the destination per deploy."""
    if cfg.type == "application":
        return ApplicationTarget(cfg, repo_ref, env_clusters)
    return ApplicationSetTarget(cfg, repo_ref)
