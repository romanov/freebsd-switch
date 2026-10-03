PYTHON?=python3.12

.PHONY: help doctor fetch firmware freebsd image validate test smoke all
help doctor fetch firmware freebsd image validate test smoke:
	${PYTHON} scripts/build.py ${.TARGET}
all:
	${PYTHON} scripts/build.py all
