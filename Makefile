# Scriptures - make, then ./build/scriptures (or make run).
#
# Needs only a C compiler: SDL2's headers are in third_party/ and the program
# links to the system's libSDL2-2.0.so.0 (present wherever SDL2 is installed).
# With the development package (libsdl2-dev), pkg-config is used instead.
# At run time: SDL2, the curl program, DejaVu fonts; libsqlite3 (optional:
# importing the Python version's library, reading Firefox's session).

PREFIX  ?= /usr/local
CC      ?= cc
CFLAGS  ?= -O2 -g
CFLAGS  += -std=c11 -D_GNU_SOURCE -Wall -Wextra -Isrc -Ithird_party/stb
LDLIBS  += -lm -lpthread -ldl

ifeq ($(shell pkg-config --exists sdl2 2>/dev/null && echo yes),yes)
SDL_CFLAGS := $(shell pkg-config --cflags sdl2)
SDL_LIBS   := $(shell pkg-config --libs sdl2)
else
SDL_CFLAGS := -Ithird_party/SDL2/include -D_REENTRANT
SDL_LIBS   := -l:libSDL2-2.0.so.0
endif

# The portable core (SIEOS too), then the Linux parts.
CORE  = util json html store layout draw ui library app panels notesview
LINUX = plat_sdl font_tt net_curl sqlite_min linux_import
OBJS  = $(addprefix build/,$(addsuffix .o,$(CORE) $(LINUX)))

build/scriptures: $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $^ $(SDL_LIBS) $(LDLIBS)

build/%.o: src/%.c src/*.h | build
	$(CC) $(CFLAGS) -c -o $@ $<

build/plat_sdl.o: src/plat_sdl.c src/plat.h | build
	$(CC) $(CFLAGS) $(SDL_CFLAGS) -c -o $@ $<

build/font_tt.o: src/font_tt.c src/font.h | build
	$(CC) $(CFLAGS) -Wno-unused-function -c -o $@ $<

build:
	mkdir -p build

run: build/scriptures
	./build/scriptures

install: build/scriptures
	PREFIX="$(PREFIX)" ./install.sh

clean:
	rm -rf build

.PHONY: run install clean
