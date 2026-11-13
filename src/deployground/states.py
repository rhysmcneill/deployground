"""Shared lifecycle states for components and stacks."""

from enum import StrEnum


class CompState(StrEnum):
    Progressing = "progressing"
    Healthy = "healthy"
    Failed = "failed"


class StackState(StrEnum):
    Requested = "requested"
    Deploying = "deploying"
    Running = "running"
    Expiring = "expiring"
    Destroying = "destroying"
    Destroyed = "destroyed"
    Failed = "failed"
