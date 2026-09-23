# RTM32 Snake + sort test

Two RTM32 programs that run on the supplied RTM32-0.5 emulator:

- `snake.rmt`: text-mode Snake game (UART, ANSI terminal).
- `test.rtm`: bubble sort of 10 signed integers with decimal UART output.

## Compatibility note

The 0.5 emulator implements the original STX4 instruction encoding, while
`rtm32.asm` 1.2.1 emits a newer encoding for several mnemonics (including
`addi` and `trap`). Therefore:

1. `snake.rmt` and `test.rtm` are written in a readable STX4-like assembly
   dialect.
2. `tools/build_snake.py` encodes the instructions with the original STX4
   bit layout (now also supporting `mul`/`div`/`rest` for the sort's decimal
   printing).
3. The 1.2.1 assembler is used only to package numeric `.word` directives
   into the MDBG image consumed by the emulator (that path is identical in
   1.2.0 and 1.2.1, byte for byte).

The build script copies the assembler to a temporary executable, so it never
changes the mode of the supplied assembler file.

`test121.rtm` is the same sort written in native 1.2.1 syntax, reserved for the
future 1.2.1 CPU. It assembles with 1.2.1 but does **not** run on the 0.5
emulator.

## Build and test

```sh
make test
make
```

Images are written to `build/snake.bin`, `build/test.bin` and
`build/test121.bin`.

## Run in the debugger

Start the emulator from the repository root:

```sh
./rtm32 -d telnet
```

In another terminal, connect to the debugger:

```sh
telnet -4 localhost 4444
```

Then load and run an image (example for the sort):

```text
load build/test.bin exact
c
```

Expected UART output for the sort:

```text
Antes:
34 -7 23 32 5 -12 62 0 9 1
Despues:
-12 -7 0 1 5 9 23 32 34 62
Fin.
```

(The sort ends in an infinite loop after printing, like the snake's
`quit_loop`/`game_over_loop`.)

Verified on RTM32-0.5: full transcript above, sorted array confirmed with a
memory dump, single run from start to `fin_prog`, zero faults.

The emulator prints a PTY path such as `/dev/pts/3` when it starts. Connect a
terminal to that PTY to see the game and send controls. For example:

```sh
socat -,raw,echo=0 /dev/pts/3,raw,echo=0
```

Snake controls are `W`, `A`, `S`, `D` (lowercase) and `Q` to stop. Use `quit`
in the debugger session to shut down the emulator.

## Machine interface used by the programs

- `0xFFFFFF00`: UART data register; writes transmit one character and reads
  consume one character.
- `0xFFFFFF04`: UART status register; bit 0 is set when input is available.
- Both programs use software delays or straight-line pacing because no timer
  peripheral is documented in the supplied kit.

The snake board is 20 columns by 10 rows. The snake can grow to 64 segments,
and food placement uses a deterministic sequence so the game does not depend
on an undocumented random-device peripheral.
