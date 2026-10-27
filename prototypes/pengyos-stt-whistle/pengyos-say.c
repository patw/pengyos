/* pengyos-say — speech to text for PengyOS, on the Cactus Whistle model.
 *
 * Reads a 16 kHz mono-capable RIFF WAV from a file or from stdin, transcribes it
 * with the Whistle .cact, and prints the transcript. One prebuilt engine, one
 * pinned model file, no interpreter, no network, no telemetry.
 *
 *   printf | pw-record - > clip.wav
 *   pw-record --target 16000 --rate 16000 --channels 1 - | pengyos-say -
 *
 * Engine: Cactus needle (Apache-2.0), model: Cactus Whistle 2.0.0 (Apache-2.0).
 * Build:
 *   cc -O2 -o pengyos-say pengyos-say.c libneedle.a -lc++ -lc++abi -lm -lpthread -ldl
 * (libneedle.a is built with clang/libc++; the shipped prebuilt `needle` binary
 * carries its runtime and needs only libc/libm/pthread/dl.)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "needle.h"

#define MODEL_DEFAULT "/usr/share/pengyos/stt/whistle.cact"
#define OUT_CAP 16384
#define KW_CAP 4096

/* Exit codes: 0 ok, 2 bad input, 3 model load, 4 engine failure. */

static int read_all(FILE *f, unsigned char **out, size_t *n) {
    size_t cap = 1 << 16, len = 0;
    unsigned char *buf = (unsigned char *)malloc(cap);
    if (!buf) return -1;
    for (;;) {
        if (len == cap) {
            size_t newcap = cap * 2;
            unsigned char *nb = (unsigned char *)realloc(buf, newcap);
            if (!nb) { free(buf); return -1; }
            buf = nb; cap = newcap;
        }
        size_t got = fread(buf + len, 1, cap - len, f);
        if (got == 0) break;
        len += got;
    }
    *out = buf; *n = len;
    return 0;
}

/* Minimal RIFF/WAV reader: 8/16/24-bit int PCM, 32-bit float, mono or stereo
   (stereo averaged). Samples normalised to [-1,1]. Returns NULL if the input is
   not a WAV we can read. */
static float *wav_to_pcm(const unsigned char *d, size_t n, long *nsamples) {
    if (n < 44 || memcmp(d, "RIFF", 4) || memcmp(d + 8, "WAVE", 4)) return NULL;
    uint16_t channels = 0, bits = 0; uint32_t rate = 0, fmt = 0;
    const unsigned char *p = d + 12, *end = d + n, *data = NULL; size_t data_len = 0;
    while (p + 8 <= end) {
        uint32_t id, sz;
        memcpy(&id, p, 4); memcpy(&sz, p + 4, 4);
        if (id == 0x20746d66u /* 'fmt ' */) {
            memcpy(&channels, p + 10, 2);
            memcpy(&fmt, p + 12, 2);
            memcpy(&rate, p + 16, 4);
            memcpy(&bits, p + 22, 2);
        } else if (id == 0x61746164u /* 'data' */) {
            data = p + 8; data_len = sz;
            if (data + data_len > end) return NULL;   /* truncated */
        }
        p += 8 + sz + (sz & 1u);
    }
    if (!data || !channels || !rate) return NULL;
    if (bits != 8 && bits != 16 && bits != 24 && bits != 32) return NULL;
    if (fmt != 1 && fmt != 3) return NULL;            /* PCM or IEEE float */
    if (fmt == 3 && bits != 32) return NULL;

    long frames = (long)(data_len / (bits / 8u) / channels);
    float *out = (float *)malloc(sizeof(float) * (size_t)(frames > 0 ? frames : 1));
    if (!out) return NULL;
    for (long i = 0; i < frames; i++) {
        double acc = 0.0;
        for (int c = 0; c < channels; c++) {
            const unsigned char *s = data + (size_t)((i * channels + c) * (bits / 8u));
            if (bits == 16) { int16_t v; memcpy(&v, s, 2); acc += v / 32768.0; }
            else if (bits == 8) { acc += ((int)*s - 128) / 128.0; }
            else if (bits == 32) {
                if (fmt == 3) { float v; memcpy(&v, s, 4); acc += v; }
                else { int32_t v; memcpy(&v, s, 4); acc += v / 2147483648.0; }
            } else { /* 24-bit */
                int32_t v = (int32_t)(s[0] | (s[1] << 8) | (s[2] << 16));
                if (v & 0x800000) v |= (int32_t)0xff000000;
                acc += v / 8388608.0;
            }
        }
        out[i] = (float)(acc / channels);
    }
    *nsamples = frames;
    return out;
}

/* Emit the value of the "text" key from the engine's JSON, unescaping \" and \n,
   without pulling in a JSON library. */
static void print_text_field(const char *json) {
    const char *t = strstr(json, "\"text\":\"");
    if (!t) { putchar('\n'); return; }
    t += 8;
    const char *q = t;
    while (*q) {
        if (*q == '\\' && q[1]) q += 2;
        else if (*q == '"') break;
        else q++;
    }
    for (const char *p = t; p < q; p++) {
        if (*p == '\\' && p + 1 < q) {
            p++;
            putchar(*p == '"' ? '"' : (*p == 'n' ? ' ' : (*p == 'n' ? ' ' : *p)));
        } else putchar(*p);
    }
    putchar('\n');
}

int main(int argc, char **argv) {
    const char *model = getenv("WHISTLE_MODEL");
    const char *path = NULL, *lang = NULL, *kwfile = NULL;
    int json_out = 0, word_ts = 0;
    if (!model) model = MODEL_DEFAULT;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-m") && i + 1 < argc) model = argv[++i];
        else if (!strcmp(argv[i], "-l") && i + 1 < argc) lang = argv[++i];
        else if (!strcmp(argv[i], "-k") && i + 1 < argc) kwfile = argv[++i];
        else if (!strcmp(argv[i], "-j")) json_out = 1;
        else if (!strcmp(argv[i], "-t")) word_ts = 1;
        else if (!strcmp(argv[i], "-h")) {
            fprintf(stderr,
              "usage: pengyos-say [-] [-m model.cact] [-l en|de|fr|es|it|nl|pl] [-k keywords] [-t] [-j]\n"
              "  reads a RIFF WAV from a file, or from stdin with '-'; writes the transcript.\n"
              "  -m model   the .cact to load (default $WHISTLE_MODEL or " MODEL_DEFAULT ")\n"
              "  -l lang    force the language (default: detect it)\n"
              "  -k file    keyword-bias list, one phrase per line\n"
              "  -t         word timestamps in the JSON\n"
              "  -j         print the engine's JSON instead of bare text\n");
            return 0;
        }
        else path = argv[i];
    }

    unsigned char *buf = NULL; size_t len = 0;
    FILE *f = (path && strcmp(path, "-")) ? fopen(path, "rb") : stdin;
    if (!f) { fprintf(stderr, "pengyos-say: cannot open %s\n", path); return 2; }
    int r = read_all(f, &buf, &len);
    if (f != stdin) fclose(f);
    if (r) return 2;

    long ns = 0;
    float *pcm = wav_to_pcm(buf, len, &ns);
    if (!pcm) { fprintf(stderr, "pengyos-say: input is not a readable PCM/float WAV\n"); return 2; }
    if (ns == 0) { puts(""); return 0; }

    unsigned char *cact = NULL; size_t cact_len = 0;
    FILE *mf = fopen(model, "rb");
    if (!mf) { fprintf(stderr, "pengyos-say: cannot open model %s\n", model); return 3; }
    if (read_all(mf, &cact, &cact_len)) { fclose(mf); return 3; }
    fclose(mf);

    if (needle_load(cact, (unsigned long long)cact_len) != 0) {
        fprintf(stderr, "pengyos-say: needle_load: %s\n", needle_last_error());
        return 3;
    }

    char keywords[KW_CAP] = {0};
    const char *kw = NULL;
    if (kwfile) {
        FILE *kf = fopen(kwfile, "rb");
        if (kf) { if (fread(keywords, 1, KW_CAP - 1, kf) > 0) kw = keywords; fclose(kf); }
    }

    char *out = (char *)malloc(OUT_CAP);
    if (!out) return 4;
    int tok = needle_transcribe(pcm, (int)ns, lang, kw, word_ts, out, OUT_CAP);
    if (tok < 0) { fprintf(stderr, "pengyos-say: %s\n", needle_last_error()); return 4; }

    if (json_out) { fputs(out, stdout); fputc('\n', stdout); }
    else print_text_field(out);

    free(out); free(cact); free(buf); free(pcm);
    return 0;
}
