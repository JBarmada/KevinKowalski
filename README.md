# KevinKowalski

## How to run things

To install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh` or look at the website [https://docs.astral.sh/uv/getting-started/installation/](https://docs.astral.sh/uv/getting-started/installation/)

To run things: `uv run <file>`.

To add a new dependency: `uv add <dependency>` e.g. `uv add numpy`. you definitely want to commit `pyproject.toml` which describes the dependencies, and it is also standard practice to include `uv.lock` although it will be built new again by uv using pyproject.toml if it isn't up to date.
