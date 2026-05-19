# SOS repo Makefile. Each target is intentionally small — call the real
# tooling, don't hide logic here.

# Belt-and-suspenders: the export script also prepends REPO_ROOT to
# sys.path, but exporting PYTHONPATH here makes the invocation portable
# for anyone calling `make`-less (e.g. CI wrappers, IDEs).
export PYTHONPATH := $(CURDIR)

.PHONY: contracts
contracts:
	python scripts/export_port_schemas.py
	bash scripts/gen_ts_types.sh

.PHONY: contracts-check
contracts-check:
	python scripts/export_port_schemas.py --check
