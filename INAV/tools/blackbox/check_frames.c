#include <fcntl.h>
#include <unistd.h>
#include <inttypes.h>
#include <stdio.h>
#include "src/parser.h"

static void frame(flightLog_t *log, bool valid, int64_t *values, uint8_t kind,
                  int fields, int offset, int size) {
    (void) log;
    printf("{\"kind\":\"%c\",\"valid\":%s,\"has_values\":%s,\"offset\":%d,\"size\":%d",
           kind, valid ? "true" : "false", values ? "true" : "false", offset, size);
    if (values && fields >= 2 && (kind == 'I' || kind == 'P')) {
        printf(",\"iteration\":%" PRId64 ",\"time_us\":%" PRId64, values[0], values[1]);
    }
    puts("}");
}
static void event(flightLog_t *log, flightLogEvent_t *ev) {
    (void) log;
    printf("{\"event\":%d}\n", ev->event);
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    int fd = open(argv[1], O_RDONLY);
    if (fd < 0) return 3;
    flightLog_t *log = flightLogCreate(fd);
    if (!log) return 4;
    bool ok = flightLogParse(log, 0, NULL, frame, event, false);
    for (int kind=0; kind<256; kind++) {
        flightLogFrameStatistics_t *s = &log->stats.frame[kind];
        if (s->validCount || s->desyncCount || s->corruptCount)
            printf("{\"stat_kind\":\"%c\",\"valid\":%u,\"desync\":%u,\"corrupt\":%u,\"bytes\":%u}\n",
                   kind, s->validCount, s->desyncCount, s->corruptCount, s->bytes);
    }
    printf("{\"parse_success\":%s,\"total_corrupt\":%u,\"intentionally_absent\":%u}\n", ok ? "true" : "false", log->stats.totalCorruptFrames, log->stats.intentionallyAbsentIterations);
    flightLogDestroy(log);
    close(fd);
    return ok ? 0 : 1;
}
