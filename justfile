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

# Show the current protected-branch rule for main.
branch-protection:
    tea api '/repos/{owner}/{repo}/branch_protections/main'

# Allow direct pushes to main while keeping the branch protected.
allow-main-push:
    tea api -X PATCH -d '{"enable_push":true}' '/repos/{owner}/{repo}/branch_protections/main'
