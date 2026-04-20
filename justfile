default:
    @just --list

run:
    pnpm dev

build:
    pnpm build

format:
    pnpm prettier --check .

lint:
    pnpm check

ci: format lint build
    @:
