# Changelog

## [0.10.1](https://github.com/rhysmcneill/deployground/compare/deployground-v0.10.0...deployground-v0.10.1) (2026-11-24)


### Bug Fixes

* stop non-system component Applications from racing for namespace ownership ([#54](https://github.com/rhysmcneill/deployground/issues/54)) ([76720e1](https://github.com/rhysmcneill/deployground/commit/76720e160c6b14486b9530adac5e47732c1f5153))

## [0.10.0](https://github.com/rhysmcneill/deployground/compare/deployground-v0.9.0...deployground-v0.10.0) (2026-11-19)


### ⚠ BREAKING CHANGES

* **config:** clean up each stack's namespace on destroy ([#49](https://github.com/rhysmcneill/deployground/issues/49))

### Features

* **config:** clean up each stack's namespace on destroy ([#49](https://github.com/rhysmcneill/deployground/issues/49)) ([cfa2529](https://github.com/rhysmcneill/deployground/commit/cfa2529cf23696f620a86d23d117a5907d23236a))
* custom Helm chart per catalog component ([#51](https://github.com/rhysmcneill/deployground/issues/51)) ([9df6d3d](https://github.com/rhysmcneill/deployground/commit/9df6d3d5d69e588c2bfdbe50748db0d2e78bc634))

## [0.9.0](https://github.com/rhysmcneill/deployground/compare/deployground-v0.8.2...deployground-v0.9.0) (2026-11-17)


### Features

* **slack:** scrap the shared channel, DM the user directly ([#47](https://github.com/rhysmcneill/deployground/issues/47)) ([714ba68](https://github.com/rhysmcneill/deployground/commit/714ba6862e0c0dd904c1bdb335957809a84e9eaa))

## [0.8.2](https://github.com/rhysmcneill/deployground/compare/deployground-v0.8.1...deployground-v0.8.2) (2026-11-17)


### Bug Fixes

* **golden-path-chart:** add tls.hosts for Tailscale ingress class ([#45](https://github.com/rhysmcneill/deployground/issues/45)) ([73950ec](https://github.com/rhysmcneill/deployground/commit/73950ec0d70cb8d1986b3dab3fcc269ef119bb68))

## [0.8.1](https://github.com/rhysmcneill/deployground/compare/deployground-v0.8.0...deployground-v0.8.1) (2026-11-17)


### Bug Fixes

* **golden-path-chart:** reference the Ingress backend port by number, not name ([#43](https://github.com/rhysmcneill/deployground/issues/43)) ([e7c292d](https://github.com/rhysmcneill/deployground/commit/e7c292ddc10c3e2b358f011ca05b4338328fbe8a))

## [0.8.0](https://github.com/rhysmcneill/deployground/compare/deployground-v0.7.0...deployground-v0.8.0) (2026-11-17)


### Features

* **deploytarget:** label every managed namespace for cluster-wide resource targeting ([#41](https://github.com/rhysmcneill/deployground/issues/41)) ([70e005b](https://github.com/rhysmcneill/deployground/commit/70e005be412c718fb26af734c8ed8238767a5152))

## [0.7.0](https://github.com/rhysmcneill/deployground/compare/deployground-v0.6.11...deployground-v0.7.0) (2026-11-17)


### ⚠ BREAKING CHANGES

* **config:** split Catalog from Stack name ([#40](https://github.com/rhysmcneill/deployground/issues/40))

### Features

* **catalog:** support {stack} in ingressHost for multi-deploy catalog stacks ([#36](https://github.com/rhysmcneill/deployground/issues/36)) ([7249a45](https://github.com/rhysmcneill/deployground/commit/7249a45a29df998d2f1c4bcdf8ee69f43acd6e1c))
* **config:** split Catalog from Stack name ([#40](https://github.com/rhysmcneill/deployground/issues/40)) ([009765f](https://github.com/rhysmcneill/deployground/commit/009765fef395192ed226e97f96048dc217b41d72))


### Bug Fixes

* **lifecycle:** extend tolerates a component dropped from the catalog ([#38](https://github.com/rhysmcneill/deployground/issues/38)) ([64cf6a2](https://github.com/rhysmcneill/deployground/commit/64cf6a2c55e292b924a36890d12542069cc808fb))
* **slack:** enable the Messages Tab in the app manifest ([#37](https://github.com/rhysmcneill/deployground/issues/37)) ([eba625f](https://github.com/rhysmcneill/deployground/commit/eba625f01966d29429586635f0da6796d6ab986a))

## [0.6.11](https://github.com/rhysmcneill/deployground/compare/deployground-v0.6.10...deployground-v0.6.11) (2026-11-17)


### Bug Fixes

* **deploytarget:** use {{path}} not {{path.path}} for the files generator directory ([#34](https://github.com/rhysmcneill/deployground/issues/34)) ([a4a9eac](https://github.com/rhysmcneill/deployground/commit/a4a9eac49ac8b24adcb39f99dab64d56e109d3f6))

## [0.6.10](https://github.com/rhysmcneill/deployground/compare/deployground-v0.6.9...deployground-v0.6.10) (2026-11-17)


### Bug Fixes

* **deploytarget:** drop the oci:// scheme from repoURL when chart is a separate field ([#32](https://github.com/rhysmcneill/deployground/issues/32)) ([2d1af8c](https://github.com/rhysmcneill/deployground/commit/2d1af8c850e1e22be53ba69b75bbf8b8648cc9b4))

## [0.6.9](https://github.com/rhysmcneill/deployground/compare/deployground-v0.6.8...deployground-v0.6.9) (2026-11-17)


### Bug Fixes

* **deploytarget:** set the OCI chart name as its own field ([#30](https://github.com/rhysmcneill/deployground/issues/30)) ([6276b91](https://github.com/rhysmcneill/deployground/commit/6276b9105936399a960aa6010008edffbcbcbf30))

## [0.6.8](https://github.com/rhysmcneill/deployground/compare/deployground-v0.6.7...deployground-v0.6.8) (2026-11-17)


### Bug Fixes

* **deployground-chart:** target the ApplicationSet's cluster by server URL or name ([#28](https://github.com/rhysmcneill/deployground/issues/28)) ([c9a525a](https://github.com/rhysmcneill/deployground/commit/c9a525a2e156f390affcaa275b8dc544ef4bd279))

## [0.6.7](https://github.com/rhysmcneill/deployground/compare/deployground-v0.6.6...deployground-v0.6.7) (2026-11-17)


### Bug Fixes

* **git:** catch the right 404 exception type from ghapi ([#26](https://github.com/rhysmcneill/deployground/issues/26)) ([92aab04](https://github.com/rhysmcneill/deployground/commit/92aab04af2cd260dc2a8e3a0fb634a971d63297d))

## [0.6.6](https://github.com/rhysmcneill/deployground/compare/deployground-v0.6.5...deployground-v0.6.6) (2026-11-17)


### Bug Fixes

* **git:** construct GhApi with sync=True ([#23](https://github.com/rhysmcneill/deployground/issues/23)) ([00d8fde](https://github.com/rhysmcneill/deployground/commit/00d8fde1b7adb65138a5866ece9cd85f9323ca23))

## [0.6.5](https://github.com/rhysmcneill/deployground/compare/deployground-v0.6.4...deployground-v0.6.5) (2026-11-17)


### Bug Fixes

* **deployground-chart:** skip no-op bootstrap applies, slightly raise OOM limit ([#21](https://github.com/rhysmcneill/deployground/issues/21)) ([1b672b4](https://github.com/rhysmcneill/deployground/commit/1b672b43746f89cf6ac1772bf43f7f0e1fd05bc6))

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
