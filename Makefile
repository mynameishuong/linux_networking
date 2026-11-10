CC = cc
CPPFLAGS += -D_POSIX_C_SOURCE=200809L -Isrc
CFLAGS ?= -std=c11 -O0 -g3 -Wall -Wextra -Wpedantic -Werror

.PHONY: all images test test-images clean
all: build/gateway build/ecu images

IMAGE_SOURCES = src/image_protocol.c src/image_io.c src/net.c
IMAGE_HEADERS = src/image_protocol.h src/image_io.h src/net.h

images: build/image_sender build/image_receiver

build:
	mkdir -p $@

build/gateway: src/gateway.c src/net.c src/net.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) src/gateway.c src/net.c $(LDFLAGS) -o $@

build/ecu: src/ecu.c src/net.c src/net.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) src/ecu.c src/net.c $(LDFLAGS) -o $@

build/image_sender: src/image_sender.c $(IMAGE_SOURCES) $(IMAGE_HEADERS) | build
	$(CC) $(CPPFLAGS) $(CFLAGS) src/image_sender.c $(IMAGE_SOURCES) $(LDFLAGS) -o $@

build/image_receiver: src/image_receiver.c $(IMAGE_SOURCES) $(IMAGE_HEADERS) | build
	$(CC) $(CPPFLAGS) $(CFLAGS) src/image_receiver.c $(IMAGE_SOURCES) $(LDFLAGS) -o $@

build/test_image_protocol: tests/test_image_protocol.c src/image_protocol.c src/image_protocol.h | build
	$(CC) $(CPPFLAGS) $(CFLAGS) tests/test_image_protocol.c src/image_protocol.c $(LDFLAGS) -o $@

test: all build/test_image_protocol
	./build/test_image_protocol
	python3 -m unittest discover -s tests -v

test-images: images build/test_image_protocol
	./build/test_image_protocol
	python3 -m unittest discover -s tests -p 'test_image_transfer.py' -v

clean:
	rm -f build/gateway build/ecu build/image_sender build/image_receiver build/test_image_protocol
