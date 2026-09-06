# God-Agent convenience targets
.PHONY: install local chat serve status settings providers test selftest clean

install:            ## install system-wide (sudo, root-capable service)
	./install.sh

local:              ## install for the current user (no sudo)
	./install.sh --local

chat:               ## normal chat interface
	python3 -m god_agent.cli chat

serve:              ## dashboard + chat UI at http://localhost:8765/
	python3 -m god_agent.cli serve

status:
	python3 -m god_agent.cli status

settings:           ## settings menu (API providers etc.)
	python3 -m god_agent.cli settings

providers:          ## list/switch API providers
	python3 -m god_agent.cli providers list

test:               ## run all tests (selftest + test_core)
	python3 -m god_agent.selftest
	python3 tests/test_core.py

selftest:           ## run zero-dependency selftests
	python3 -m god_agent.selftest

clean:
	rm -rf god_agent/__pycache__ god_agent/tools/__pycache__ tests/__pycache__
