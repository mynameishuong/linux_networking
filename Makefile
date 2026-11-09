CC = cc
CPPFLAGS += -D_POSIX_C_SOURCE=200809L -Isrc
CFLAGS ?= -std=c11 -O0 -g3 -Wall -Wextra -Wpedantic -Werror

.PHONY: all test clean
all: build/gateway build/ecu

build:
	mkdir -p $@

build/gateway: src/gateway.c src/net.c src/net.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) src/gateway.c src/net.c $(LDFLAGS) -o $@

build/ecu: src/ecu.c src/net.c src/net.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) src/ecu.c src/net.c $(LDFLAGS) -o $@

test: all
	python3 -m unittest discover -s tests -v

clean:
	rm -f build/gateway build/ecu
