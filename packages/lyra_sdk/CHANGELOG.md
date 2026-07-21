# Changelog

## [0.13.0](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.12.0...lyra-sdk-v0.13.0) (2026-07-21)


### Features

* Add dynamic API generation ([e39bca5](https://github.com/RodolfoFigueroa/lyra/commit/e39bca591cfac29d6cf5bee1055c2d78f29eab68))
* Add fractional area metrics ([fb4277b](https://github.com/RodolfoFigueroa/lyra/commit/fb4277b236ae00d6ff4c4381f4bffbd68d28300c))
* Add job operation compatibility ([394ed33](https://github.com/RodolfoFigueroa/lyra/commit/394ed3363b3ab82bd7b5f405fb005a678b075167))
* Add metric discovery ([65e1e9c](https://github.com/RodolfoFigueroa/lyra/commit/65e1e9c450a7b2645c019c093fb06d47b4bd42dd))
* Add missing docstrings and misc. lint fixes ([2cd9b2b](https://github.com/RodolfoFigueroa/lyra/commit/2cd9b2bf8c91ffaaa146c9e081a741b59215b9b0))
* Add observability API routes ([83b1a23](https://github.com/RodolfoFigueroa/lyra/commit/83b1a2336af3216d175f3814f63fd0d688255ce5))
* Add phase 5 ([6215f95](https://github.com/RodolfoFigueroa/lyra/commit/6215f95f145d81de25ccadec64f59284df177513))
* Add pre-commit integration ([d1b366c](https://github.com/RodolfoFigueroa/lyra/commit/d1b366ce2484f2f3f1e65e24fe9361076cbf0eda))
* Add support for batched columns ([c9098f2](https://github.com/RodolfoFigueroa/lyra/commit/c9098f2ee13ef90e2f8fbd905343a296499e2952))
* Add support for naming batched columns ([8116f7c](https://github.com/RodolfoFigueroa/lyra/commit/8116f7c340e5041b5b1685f50ee677b34555c092))
* Centralize job event handling ([0dd9ece](https://github.com/RodolfoFigueroa/lyra/commit/0dd9ecef298504af47547a84450595b4c44f429a))
* Centralize plugin mutation actions ([5273dcc](https://github.com/RodolfoFigueroa/lyra/commit/5273dcc5855331c05e92924a188a31694699b535))
* Change metric schema ([f246346](https://github.com/RodolfoFigueroa/lyra/commit/f246346c9363d9de88f28f198f54645f79dc96c1))
* Couple DB with worker state ([4dbcaa8](https://github.com/RodolfoFigueroa/lyra/commit/4dbcaa87e1361fbed970be9a59e300902dce28a3))
* Derive metric schema from function definitions ([348be93](https://github.com/RodolfoFigueroa/lyra/commit/348be932511f5d62086484c2433c48440e230850))
* Expose more models in the SDK ([413e185](https://github.com/RodolfoFigueroa/lyra/commit/413e185d88c85bc3cdd7709075a4185097732136))
* Implement background status cache ([bdcf5ad](https://github.com/RodolfoFigueroa/lyra/commit/bdcf5ad35f45c6b9e4f04bceb7db33b080b99f41))
* Implement first step of migration plan ([b8d7bb2](https://github.com/RodolfoFigueroa/lyra/commit/b8d7bb270b6fc1fb1765ad5ba8a8c832245dcc73))
* Implement second migration step ([41042c9](https://github.com/RodolfoFigueroa/lyra/commit/41042c9231757701cd9aa7ffa7d2d3340a77d6ff))
* Implement stage 3 ([8161df0](https://github.com/RodolfoFigueroa/lyra/commit/8161df062ffad0c4fde6af4df66175a43feb2cfd))
* Implement step 1 of v3 schema ([f49326c](https://github.com/RodolfoFigueroa/lyra/commit/f49326c885a27db8acd3082abac8068563a6c7b0))
* Implement step 1 of worker migration ([a605f60](https://github.com/RodolfoFigueroa/lyra/commit/a605f60f5c863d08696b78438879bba1d2b8870e))
* Implement step 2 of migration ([1b2950b](https://github.com/RodolfoFigueroa/lyra/commit/1b2950b62f03f2eb502fbbb2ba17d897496e3f79))
* Implement step 5 ([3168cd9](https://github.com/RodolfoFigueroa/lyra/commit/3168cd9b71b1ef8a928f5edb9b999c593a9bd97f))
* Improve database connection handling ([2336e6b](https://github.com/RodolfoFigueroa/lyra/commit/2336e6b6bcc9f6ffbf4cec6f6cf8926a53040615))
* Improve SDK and data endpoints ([0dcecd8](https://github.com/RodolfoFigueroa/lyra/commit/0dcecd8b596b042ef732a2ad30ede37ba21bd246))
* Improve serialization methods ([4a79649](https://github.com/RodolfoFigueroa/lyra/commit/4a79649b07018998735711f24b3d82ecc0ff40b9))
* **jobs:** deduplicate idempotent submissions ([db6c422](https://github.com/RodolfoFigueroa/lyra/commit/db6c42261afc0bfa375d03f942b402d84294b1d0))
* **jobs:** persist immutable run provenance ([f00215c](https://github.com/RodolfoFigueroa/lyra/commit/f00215c78481048b06e615c893703178d368c9d9))
* Make location arg mandatory ([dad96f9](https://github.com/RodolfoFigueroa/lyra/commit/dad96f96691b512d9d8c4d2d7cba4a30cd38532c))
* **mcp:** add agent discovery utilities ([f50506b](https://github.com/RodolfoFigueroa/lyra/commit/f50506b6827845d18768b22c251e92c9a45afd5a))
* Move queue config from manifest to core ([f397f04](https://github.com/RodolfoFigueroa/lyra/commit/f397f045d6e6b1d40c759ee92ec0b8126b27695e))
* Move repo config and delete routes ([56d0fc2](https://github.com/RodolfoFigueroa/lyra/commit/56d0fc2c94ea04cb13da661f5561a1349fd817b8))
* Narrow accepted metric outputs ([3fda96c](https://github.com/RodolfoFigueroa/lyra/commit/3fda96c636b07a9d5720f097cacb9088f0a60f6f))
* Narrow user-supplied arg hints ([33f869b](https://github.com/RodolfoFigueroa/lyra/commit/33f869b479f7415cc53fb238313a1d9c9743dcf9))
* Offload input description to decorator ([ad1ebf9](https://github.com/RodolfoFigueroa/lyra/commit/ad1ebf9058d9aa5cfb5cb64cb8fc88628fef891c))
* Overhaul MCP into a more understandable version ([2e72902](https://github.com/RodolfoFigueroa/lyra/commit/2e72902f637f931adf29dc5545eb85882920a8ea))
* Refactor types and tighten assertions ([7d6264f](https://github.com/RodolfoFigueroa/lyra/commit/7d6264fb31b8fbcc16dff060f46f32d4cf7a59bc))
* Remove obsolete routes ([0e65c6c](https://github.com/RodolfoFigueroa/lyra/commit/0e65c6c0f4e7e0a6bf0eabb8e97c2875a45b04cc))
* Reorg routes ([1369ee6](https://github.com/RodolfoFigueroa/lyra/commit/1369ee6a55a7cd28a5e2d5c591a6cf0f50c3f838))
* **results:** expose reproducible result descriptors ([c68150f](https://github.com/RodolfoFigueroa/lyra/commit/c68150fd3664ead0f72be27f3ead76c732ffdcb7))
* Tighten spatial models ([7b8131a](https://github.com/RodolfoFigueroa/lyra/commit/7b8131af9e4b654f53474596f24a0ffa44aebb19))


### Bug Fixes

* Add missing docstrings ([aa39841](https://github.com/RodolfoFigueroa/lyra/commit/aa398415770add15408eab52fda1017a32779f2f))
* Fix exported symbols ([45ae2b7](https://github.com/RodolfoFigueroa/lyra/commit/45ae2b74394d1b7b9c02835555220d272a64dbd0))
* Fix SDK CI integration ([53ac1b5](https://github.com/RodolfoFigueroa/lyra/commit/53ac1b5abb13c906f274d410360a9c6808d5962b))
* Fix SDK encoding issues in Windows ([efda45d](https://github.com/RodolfoFigueroa/lyra/commit/efda45da9c57131bb9173069652b243c34ea297d))
* Fix wrong generated SDK ([2ac7509](https://github.com/RodolfoFigueroa/lyra/commit/2ac7509794d1096532592adcd71e03179ad49872))

## [0.12.0](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.11.0...lyra-sdk-v0.12.0) (2026-07-21)


### Features

* Add missing docstrings and misc. lint fixes ([2cd9b2b](https://github.com/RodolfoFigueroa/lyra/commit/2cd9b2bf8c91ffaaa146c9e081a741b59215b9b0))

## [0.11.0](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.10.1...lyra-sdk-v0.11.0) (2026-07-21)


### Features

* Add dynamic API generation ([e39bca5](https://github.com/RodolfoFigueroa/lyra/commit/e39bca591cfac29d6cf5bee1055c2d78f29eab68))

## [0.10.1](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.10.0...lyra-sdk-v0.10.1) (2026-07-21)


### Bug Fixes

* Add missing docstrings ([aa39841](https://github.com/RodolfoFigueroa/lyra/commit/aa398415770add15408eab52fda1017a32779f2f))

## [0.10.0](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.9.0...lyra-sdk-v0.10.0) (2026-07-21)


### Features

* Centralize job event handling ([0dd9ece](https://github.com/RodolfoFigueroa/lyra/commit/0dd9ecef298504af47547a84450595b4c44f429a))

## [0.9.0](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.8.0...lyra-sdk-v0.9.0) (2026-07-21)


### Features

* Add pre-commit integration ([d1b366c](https://github.com/RodolfoFigueroa/lyra/commit/d1b366ce2484f2f3f1e65e24fe9361076cbf0eda))

## [0.8.0](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.7.0...lyra-sdk-v0.8.0) (2026-07-21)


### Features

* Change metric schema ([f246346](https://github.com/RodolfoFigueroa/lyra/commit/f246346c9363d9de88f28f198f54645f79dc96c1))

## [0.7.0](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.6.0...lyra-sdk-v0.7.0) (2026-07-21)


### Features

* Couple DB with worker state ([4dbcaa8](https://github.com/RodolfoFigueroa/lyra/commit/4dbcaa87e1361fbed970be9a59e300902dce28a3))

## [0.6.0](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.5.0...lyra-sdk-v0.6.0) (2026-07-21)


### Features

* Offload input description to decorator ([ad1ebf9](https://github.com/RodolfoFigueroa/lyra/commit/ad1ebf9058d9aa5cfb5cb64cb8fc88628fef891c))
* Refactor types and tighten assertions ([7d6264f](https://github.com/RodolfoFigueroa/lyra/commit/7d6264fb31b8fbcc16dff060f46f32d4cf7a59bc))

## [0.5.0](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.4.0...lyra-sdk-v0.5.0) (2026-07-20)


### Features

* Narrow user-supplied arg hints ([33f869b](https://github.com/RodolfoFigueroa/lyra/commit/33f869b479f7415cc53fb238313a1d9c9743dcf9))

## [0.4.0](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.3.0...lyra-sdk-v0.4.0) (2026-07-20)


### Features

* Derive metric schema from function definitions ([348be93](https://github.com/RodolfoFigueroa/lyra/commit/348be932511f5d62086484c2433c48440e230850))

## [0.3.0](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.2.0...lyra-sdk-v0.3.0) (2026-07-20)


### Features

* Add fractional area metrics ([fb4277b](https://github.com/RodolfoFigueroa/lyra/commit/fb4277b236ae00d6ff4c4381f4bffbd68d28300c))

## [0.2.0](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.1.3...lyra-sdk-v0.2.0) (2026-07-20)


### Features

* Improve database connection handling ([2336e6b](https://github.com/RodolfoFigueroa/lyra/commit/2336e6b6bcc9f6ffbf4cec6f6cf8926a53040615))


### Bug Fixes

* Fix wrong generated SDK ([2ac7509](https://github.com/RodolfoFigueroa/lyra/commit/2ac7509794d1096532592adcd71e03179ad49872))

## [0.1.3](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.1.2...lyra-sdk-v0.1.3) (2026-07-20)


### Bug Fixes

* Fix SDK CI integration ([53ac1b5](https://github.com/RodolfoFigueroa/lyra/commit/53ac1b5abb13c906f274d410360a9c6808d5962b))

## [0.1.2](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.1.1...lyra-sdk-v0.1.2) (2026-07-19)


### Bug Fixes

* Fix SDK CI integration ([53ac1b5](https://github.com/RodolfoFigueroa/lyra/commit/53ac1b5abb13c906f274d410360a9c6808d5962b))

## [0.1.1](https://github.com/RodolfoFigueroa/lyra/compare/lyra-sdk-v0.1.0...lyra-sdk-v0.1.1) (2026-07-18)


### Bug Fixes

* Fix SDK CI integration ([53ac1b5](https://github.com/RodolfoFigueroa/lyra/commit/53ac1b5abb13c906f274d410360a9c6808d5962b))

## 0.1.0 (2026-07-18)


### Features

* Add job operation compatibility ([394ed33](https://github.com/RodolfoFigueroa/lyra/commit/394ed3363b3ab82bd7b5f405fb005a678b075167))
* Add metric discovery ([65e1e9c](https://github.com/RodolfoFigueroa/lyra/commit/65e1e9c450a7b2645c019c093fb06d47b4bd42dd))
* Add observability API routes ([83b1a23](https://github.com/RodolfoFigueroa/lyra/commit/83b1a2336af3216d175f3814f63fd0d688255ce5))
* Add phase 5 ([6215f95](https://github.com/RodolfoFigueroa/lyra/commit/6215f95f145d81de25ccadec64f59284df177513))
* Add support for batched columns ([c9098f2](https://github.com/RodolfoFigueroa/lyra/commit/c9098f2ee13ef90e2f8fbd905343a296499e2952))
* Add support for naming batched columns ([8116f7c](https://github.com/RodolfoFigueroa/lyra/commit/8116f7c340e5041b5b1685f50ee677b34555c092))
* Centralize plugin mutation actions ([5273dcc](https://github.com/RodolfoFigueroa/lyra/commit/5273dcc5855331c05e92924a188a31694699b535))
* Expose more models in the SDK ([413e185](https://github.com/RodolfoFigueroa/lyra/commit/413e185d88c85bc3cdd7709075a4185097732136))
* Implement background status cache ([bdcf5ad](https://github.com/RodolfoFigueroa/lyra/commit/bdcf5ad35f45c6b9e4f04bceb7db33b080b99f41))
* Implement first step of migration plan ([b8d7bb2](https://github.com/RodolfoFigueroa/lyra/commit/b8d7bb270b6fc1fb1765ad5ba8a8c832245dcc73))
* Implement second migration step ([41042c9](https://github.com/RodolfoFigueroa/lyra/commit/41042c9231757701cd9aa7ffa7d2d3340a77d6ff))
* Implement stage 3 ([8161df0](https://github.com/RodolfoFigueroa/lyra/commit/8161df062ffad0c4fde6af4df66175a43feb2cfd))
* Implement step 1 of v3 schema ([f49326c](https://github.com/RodolfoFigueroa/lyra/commit/f49326c885a27db8acd3082abac8068563a6c7b0))
* Implement step 1 of worker migration ([a605f60](https://github.com/RodolfoFigueroa/lyra/commit/a605f60f5c863d08696b78438879bba1d2b8870e))
* Implement step 2 of migration ([1b2950b](https://github.com/RodolfoFigueroa/lyra/commit/1b2950b62f03f2eb502fbbb2ba17d897496e3f79))
* Implement step 5 ([3168cd9](https://github.com/RodolfoFigueroa/lyra/commit/3168cd9b71b1ef8a928f5edb9b999c593a9bd97f))
* Improve SDK and data endpoints ([0dcecd8](https://github.com/RodolfoFigueroa/lyra/commit/0dcecd8b596b042ef732a2ad30ede37ba21bd246))
* Improve serialization methods ([4a79649](https://github.com/RodolfoFigueroa/lyra/commit/4a79649b07018998735711f24b3d82ecc0ff40b9))
* **jobs:** deduplicate idempotent submissions ([db6c422](https://github.com/RodolfoFigueroa/lyra/commit/db6c42261afc0bfa375d03f942b402d84294b1d0))
* **jobs:** persist immutable run provenance ([f00215c](https://github.com/RodolfoFigueroa/lyra/commit/f00215c78481048b06e615c893703178d368c9d9))
* Make location arg mandatory ([dad96f9](https://github.com/RodolfoFigueroa/lyra/commit/dad96f96691b512d9d8c4d2d7cba4a30cd38532c))
* **mcp:** add agent discovery utilities ([f50506b](https://github.com/RodolfoFigueroa/lyra/commit/f50506b6827845d18768b22c251e92c9a45afd5a))
* Move queue config from manifest to core ([f397f04](https://github.com/RodolfoFigueroa/lyra/commit/f397f045d6e6b1d40c759ee92ec0b8126b27695e))
* Move repo config and delete routes ([56d0fc2](https://github.com/RodolfoFigueroa/lyra/commit/56d0fc2c94ea04cb13da661f5561a1349fd817b8))
* Narrow accepted metric outputs ([3fda96c](https://github.com/RodolfoFigueroa/lyra/commit/3fda96c636b07a9d5720f097cacb9088f0a60f6f))
* Overhaul MCP into a more understandable version ([2e72902](https://github.com/RodolfoFigueroa/lyra/commit/2e72902f637f931adf29dc5545eb85882920a8ea))
* Remove obsolete routes ([0e65c6c](https://github.com/RodolfoFigueroa/lyra/commit/0e65c6c0f4e7e0a6bf0eabb8e97c2875a45b04cc))
* Reorg routes ([1369ee6](https://github.com/RodolfoFigueroa/lyra/commit/1369ee6a55a7cd28a5e2d5c591a6cf0f50c3f838))
* **results:** expose reproducible result descriptors ([c68150f](https://github.com/RodolfoFigueroa/lyra/commit/c68150fd3664ead0f72be27f3ead76c732ffdcb7))
* Tighten spatial models ([7b8131a](https://github.com/RodolfoFigueroa/lyra/commit/7b8131af9e4b654f53474596f24a0ffa44aebb19))


### Bug Fixes

* Fix exported symbols ([45ae2b7](https://github.com/RodolfoFigueroa/lyra/commit/45ae2b74394d1b7b9c02835555220d272a64dbd0))
* Fix SDK encoding issues in Windows ([efda45d](https://github.com/RodolfoFigueroa/lyra/commit/efda45da9c57131bb9173069652b243c34ea297d))
