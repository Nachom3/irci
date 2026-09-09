PYTHON ?= python3
BUILD_DIR := build
SNAKE_IMAGE := $(BUILD_DIR)/snake.bin

.PHONY: all build test clean

all: build

build: $(SNAKE_IMAGE)

$(SNAKE_IMAGE): snake.stx4 tools/build_snake.py rtm32.asm-1.2.0/x86_64-linux-musl-rtm32.asm
	@mkdir -p $(BUILD_DIR)
	"$(PYTHON)" tools/build_snake.py --source snake.stx4 --output "$@" --assembler rtm32.asm-1.2.0/x86_64-linux-musl-rtm32.asm

test:
	$(PYTHON) -m unittest discover -s tests -v

clean:
	rm -rf $(BUILD_DIR)
