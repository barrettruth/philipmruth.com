default:
    @just --list

install:
    pnpm install --frozen-lockfile

run: install
    pnpm dev

build: install
    pnpm build

format: install
    pnpm exec biome format .

lint: install
    pnpm check

ci: format lint build
    @:
