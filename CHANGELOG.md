# Changelog

## [0.6.4](https://github.com/rhysmcneill/deployground/compare/deployground-v0.6.3...deployground-v0.6.4) (2026-11-17)


### Bug Fixes

* appset repo URL + config validation errors from service-link env vars ([#19](https://github.com/rhysmcneill/deployground/issues/19)) ([3e9523a](https://github.com/rhysmcneill/deployground/commit/3e9523a660479913e49f05ceea4d35031fc24fcd))

## [0.6.3](https://github.com/rhysmcneill/deployground/compare/deployground-v0.6.2...deployground-v0.6.3) (2026-11-17)


### Bug Fixes

* **deployground-chart:** switch bootstrap kubectl image off bitnami ([#17](https://github.com/rhysmcneill/deployground/issues/17)) ([298852e](https://github.com/rhysmcneill/deployground/commit/298852e891bce4c819bcd4eb5702e9644b71d6ea))

## [0.6.2](https://github.com/rhysmcneill/deployground/compare/deployground-v0.6.1...deployground-v0.6.2) (2026-11-17)


### Bug Fixes

* **deployground-chart:** bootstrap ConfigMap is also a PreSync hook ([#15](https://github.com/rhysmcneill/deployground/issues/15)) ([7e1b5d4](https://github.com/rhysmcneill/deployground/commit/7e1b5d4e7c02ee25a431dc444765547125968b76))

## [0.6.1](https://github.com/rhysmcneill/deployground/compare/deployground-v0.6.0...deployground-v0.6.1) (2026-11-17)


### Bug Fixes

* **deployground-chart:** bootstrap hook ordering and missing resources ([#13](https://github.com/rhysmcneill/deployground/issues/13)) ([db495c1](https://github.com/rhysmcneill/deployground/commit/db495c17150373ebae967ff1bff92f36093c9fe3))

## [0.6.0](https://github.com/rhysmcneill/deployground/compare/deployground-v0.5.0...deployground-v0.6.0) (2026-11-17)


### Features

* **deployground-chart:** auto-apply the ApplicationSet only, never notifications-cm ([#11](https://github.com/rhysmcneill/deployground/issues/11)) ([2de1fca](https://github.com/rhysmcneill/deployground/commit/2de1fca2d95e6ba925635952fef1fe38816fc559))

## [0.5.0](https://github.com/rhysmcneill/deployground/compare/deployground-v0.4.0...deployground-v0.5.0) (2026-11-17)


### Features

* expose ingressClassName on Component ([#9](https://github.com/rhysmcneill/deployground/issues/9)) ([b78d54a](https://github.com/rhysmcneill/deployground/commit/b78d54a00b29d98802fc97212a17a91f2fc17a35))

## [0.4.0](https://github.com/rhysmcneill/deployground/compare/deployground-v0.3.0...deployground-v0.4.0) (2026-11-17)


### Features

* enable private-registry pulls (release-please backfill) ([#6](https://github.com/rhysmcneill/deployground/issues/6)) ([9d10ad6](https://github.com/rhysmcneill/deployground/commit/9d10ad675eb053b757884a83367650e9284badc6))


### Bug Fixes

* **release:** un-link chart versions from the bot's own version ([#8](https://github.com/rhysmcneill/deployground/issues/8)) ([2d518f2](https://github.com/rhysmcneill/deployground/commit/2d518f28253d2bc12e042eb61fe834351e2f41b5))

## [0.3.0](https://github.com/rhysmcneill/deployground/compare/deployground-v0.2.0...deployground-v0.3.0) (2026-11-17)


### Features

* publish golden-path as a versioned oci chart, drop manual copy requirement ([#3](https://github.com/rhysmcneill/deployground/issues/3)) ([7674b21](https://github.com/rhysmcneill/deployground/commit/7674b211620f41030a97f1bc5237bbfb7bfad0bd))

## [0.2.0](https://github.com/rhysmcneill/deployground/compare/deployground-v0.1.0...deployground-v0.2.0) (2026-11-16)


### Features

* Deployground Step 1 — Slack GitOps deploy bot ([#1](https://github.com/rhysmcneill/deployground/issues/1)) ([554a5f8](https://github.com/rhysmcneill/deployground/commit/554a5f8499148c6de7df27c8eb66897b8d0d6453))
