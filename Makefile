PYTHON ?= python3
BUILD_DIR := build
ASSEMBLER := rtm32.asm-1.2.1/x86_64-linux-musl-rtm32.asm
SNAKE_IMAGE := $(BUILD_DIR)/snake.bin
SORT_IMAGE := $(BUILD_DIR)/test.bin
TEST121_IMAGE := $(BUILD_DIR)/test121.bin
STAGED_ASSEMBLER := $(BUILD_DIR)/rtm32.asm

.PHONY: all build test clean

all: build

build: $(SNAKE_IMAGE) $(SORT_IMAGE) $(TEST121_IMAGE)

# snake.rmt and test.rtm use the old STX4 dialect understood by the 0.5
# emulator: build_snake.py encodes old words and uses 1.2.1 only as packager.
$(SNAKE_IMAGE): snake.rmt tools/build_snake.py $(ASSEMBLER)
	@mkdir -p $(BUILD_DIR)
	"$(PYTHON)" tools/build_snake.py --source snake.rmt --output "$@" --assembler $(ASSEMBLER)

$(SORT_IMAGE): test.rtm tools/build_snake.py $(ASSEMBLER)
	@mkdir -p $(BUILD_DIR)
	"$(PYTHON)" tools/build_snake.py --source test.rtm --output "$@" --assembler $(ASSEMBLER)

# test121.rtm is native 1.2.1 syntax (future CPU): assembled directly.
# The supplied file ships without the executable bit, so stage a copy.
$(STAGED_ASSEMBLER): $(ASSEMBLER)
	@mkdir -p $(BUILD_DIR)
	@cp "$(ASSEMBLER)" "$@"
	@chmod +x "$@"

$(TEST121_IMAGE): test121.rtm $(STAGED_ASSEMBLER)
	"$(STAGED_ASSEMBLER)" test121.rtm -o "$@"

test:
	$(PYTHON) -m unittest discover -s tests -v

clean:
	rm -rf $(BUILD_DIR)
