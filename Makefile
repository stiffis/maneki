.PHONY: check

check:
	python3 -I -m unittest discover -s tests -t . -v
