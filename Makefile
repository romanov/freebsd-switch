PYTHON?=python3.12

.PHONY: help doctor fetch firmware freebsd pkgbase image validate test smoke serve all
help doctor fetch firmware freebsd pkgbase image validate test smoke serve:
	${PYTHON} scripts/build.py ${.TARGET}
all:
	${PYTHON} scripts/build.py all
