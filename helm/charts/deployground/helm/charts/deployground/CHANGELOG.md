# Changelog

## [0.7.0](https://github.com/rhysmcneill/deployground/compare/deployground-chart-v0.6.0...deployground-chart-v0.7.0) (2026-11-17)


### Features

* **slack:** scrap the shared channel, DM the user directly ([#47](https://github.com/rhysmcneill/deployground/issues/47)) ([714ba68](https://github.com/rhysmcneill/deployground/commit/714ba6862e0c0dd904c1bdb335957809a84e9eaa))

## [0.6.0](https://github.com/rhysmcneill/deployground/compare/deployground-chart-v0.5.5...deployground-chart-v0.6.0) (2026-11-17)


### ⚠ BREAKING CHANGES

* **config:** split Catalog from Stack name ([#40](https://github.com/rhysmcneill/deployground/issues/40))

### Features

* **config:** split Catalog from Stack name ([#40](https://github.com/rhysmcneill/deployground/issues/40)) ([009765f](https://github.com/rhysmcneill/deployground/commit/009765fef395192ed226e97f96048dc217b41d72))

## [0.5.5](https://github.com/rhysmcneill/deployground/compare/deployground-chart-v0.5.4...deployground-chart-v0.5.5) (2026-11-17)


### Bug Fixes

* **deployground-chart:** skip no-op bootstrap applies, slightly raise OOM limit ([#21](https://github.com/rhysmcneill/deployground/issues/21)) ([1b672b4](https://github.com/rhysmcneill/deployground/commit/1b672b43746f89cf6ac1772bf43f7f0e1fd05bc6))

## [0.5.4](https://github.com/rhysmcneill/deployground/compare/deployground-chart-v0.5.3...deployground-chart-v0.5.4) (2026-11-17)


### Bug Fixes

* appset repo URL + config validation errors from service-link env vars ([#19](https://github.com/rhysmcneill/deployground/issues/19)) ([3e9523a](https://github.com/rhysmcneill/deployground/commit/3e9523a660479913e49f05ceea4d35031fc24fcd))

## [0.5.3](https://github.com/rhysmcneill/deployground/compare/deployground-chart-v0.5.2...deployground-chart-v0.5.3) (2026-11-17)


### Bug Fixes

* **deployground-chart:** switch bootstrap kubectl image off bitnami ([#17](https://github.com/rhysmcneill/deployground/issues/17)) ([298852e](https://github.com/rhysmcneill/deployground/commit/298852e891bce4c819bcd4eb5702e9644b71d6ea))

## [0.5.2](https://github.com/rhysmcneill/deployground/compare/deployground-chart-v0.5.1...deployground-chart-v0.5.2) (2026-11-17)


### Bug Fixes

* **deployground-chart:** bootstrap ConfigMap is also a PreSync hook ([#15](https://github.com/rhysmcneill/deployground/issues/15)) ([7e1b5d4](https://github.com/rhysmcneill/deployground/commit/7e1b5d4e7c02ee25a431dc444765547125968b76))

## [0.5.1](https://github.com/rhysmcneill/deployground/compare/deployground-chart-v0.5.0...deployground-chart-v0.5.1) (2026-11-17)


### Bug Fixes

* **deployground-chart:** bootstrap hook ordering and missing resources ([#13](https://github.com/rhysmcneill/deployground/issues/13)) ([db495c1](https://github.com/rhysmcneill/deployground/commit/db495c17150373ebae967ff1bff92f36093c9fe3))

## [0.5.0](https://github.com/rhysmcneill/deployground/compare/deployground-chart-v0.4.0...deployground-chart-v0.5.0) (2026-11-17)


### Features

* **deployground-chart:** auto-apply the ApplicationSet only, never notifications-cm ([#11](https://github.com/rhysmcneill/deployground/issues/11)) ([2de1fca](https://github.com/rhysmcneill/deployground/commit/2de1fca2d95e6ba925635952fef1fe38816fc559))

## [0.4.0](https://github.com/rhysmcneill/deployground/compare/deployground-chart-v0.3.0...deployground-chart-v0.4.0) (2026-11-17)


### Features

* enable private-registry pulls (release-please backfill) ([#6](https://github.com/rhysmcneill/deployground/issues/6)) ([9d10ad6](https://github.com/rhysmcneill/deployground/commit/9d10ad675eb053b757884a83367650e9284badc6))

## [0.3.0](https://github.com/rhysmcneill/deployground/compare/deployground-chart-v0.1.0...deployground-chart-v0.3.0) (2026-11-17)


### Features

* publish golden-path as a versioned oci chart, drop manual copy requirement ([#3](https://github.com/rhysmcneill/deployground/issues/3)) ([7674b21](https://github.com/rhysmcneill/deployground/commit/7674b211620f41030a97f1bc5237bbfb7bfad0bd))
