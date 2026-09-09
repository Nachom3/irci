# RTM32 Snake

A text-mode Snake game for the supplied RTM32-0.5 emulator.

## Important compatibility note

The supplied emulator implements the original STX4 instruction encoding. The
supplied `rtm32.asm-1.2.0` assembler uses a newer encoding for several
mnemonics, including `addi` and `trap`. Therefore:

1. `snake.rmt` is written in a readable STX4-like assembly dialect.
2. `tools/build_snake.py` encodes the instructions with the original STX4
   bit layout.
3. The official assembler is used only to package numeric `.word` directives
   into the MDBG image consumed by the emulator.

The build script copies the assembler to a temporary executable, so it never
changes the mode of the supplied assembler file.

## Build and test

```sh
make test
make
```

The image is written to `build/snake.bin`.

## Run in the debugger

Start the emulator from the repository root:

```sh
./rtm32 -d telnet
```

In another terminal, connect to the debugger:

```sh
telnet -4 localhost 4444
```

Then load and run the image:

```text
load build/snake.bin exact
c
```

The emulator prints a PTY path such as `/dev/pts/3` when it starts. Connect a
terminal to that PTY to see the game and send controls. For example:

```sh
socat -,raw,echo=0 /dev/pts/3,raw,echo=0
```

Controls are `W`, `A`, `S`, `D` (lowercase) and `Q` to stop. Use `quit` in
the debugger session to shut down the emulator.

## Machine interface used by the game

- `0xFFFFFF00`: UART data register; writes transmit one character and reads
  consume one character.
- `0xFFFFFF04`: UART status register; bit 0 is set when input is available.
- The game uses a software delay because no timer peripheral is documented in
  the supplied kit.

The board is 20 columns by 10 rows. The snake can grow to 64 segments, and
food placement uses a deterministic sequence so the game does not depend on an
undocumented random-device peripheral.
