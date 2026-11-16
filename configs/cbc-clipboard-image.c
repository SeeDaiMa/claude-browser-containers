/* Own the X CLIPBOARD as image/png so Chromium can paste a host screenshot.
 * TigerVNC 1.16 only bridges text, so this process is the image owner.
 * It offers image/png and not STRING, and Xvnc therefore does not treat the
 * picture as a text clipboard update.
 *
 * Rebuild: docker run --rm --platform linux/arm64 -v "$PWD:/src" -w /src \
 *   alpine:3.24 sh -c 'apk add --no-cache gcc musl-dev libx11-dev &&
 *   gcc -O2 -Wall -Wextra -o configs/bin/cbc-clipboard-image configs/cbc-clipboard-image.c -lX11'
 */
#define _POSIX_C_SOURCE 200809L

#include <X11/Xatom.h>
#include <X11/Xlib.h>

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <time.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

#define SOCKET_PATH "/tmp/cbc-clipboard-image.sock"
#define LOCK_PATH "/tmp/cbc-clipboard-image.lock"
#define MAX_IMAGE_BYTES (20u * 1024u * 1024u)
#define MAX_HEADER_BYTES 16384u

static Display *display;
static Window window;
static Atom atom_clipboard;
static Atom atom_targets;
static Atom atom_png;
static Atom atom_incr;
static Atom atom_timestamp;
static Time selection_time;
static unsigned char *png_bytes;
static size_t png_length;
static int selection_owned;
static int image_served;

static void note_status(const char *text) {
    FILE *status = fopen("/tmp/cbc-clipboard-image.status", "w");
    if (status != NULL) {
        fputs(text, status);
        fclose(status);
    }
}
static int image_generation;
static size_t max_chunk_bytes;
static volatile sig_atomic_t stop_requested;

struct incr_transfer {
    int active;
    int generation;
    Window requestor;
    Atom property;
    size_t offset;
};

static struct incr_transfer incr;

static int ignore_x_error(Display *unused_display, XErrorEvent *error) {
    (void)unused_display;
    fprintf(stderr, "cbc-clipboard-image: X error %d\n", error->error_code);
    return 0;
}

static void request_stop(int signal_number) {
    (void)signal_number;
    stop_requested = 1;
}

static size_t property_limit(Display *dpy) {
    unsigned long units = XExtendedMaxRequestSize(dpy);
    unsigned long bytes;

    if (units == 0) {
        units = XMaxRequestSize(dpy);
    }
    if (units < 128) {
        return 4096;
    }
    bytes = (units - 64) * 4;
    if (bytes > 8u * 1024u * 1024u) {
        bytes = 8u * 1024u * 1024u;
    }
    return (size_t)bytes;
}

static int png_signature(const unsigned char *data, size_t length) {
    static const unsigned char signature[8] = {137, 80, 78, 71, 13, 10, 26, 10};
    return length >= sizeof signature && memcmp(data, signature, sizeof signature) == 0;
}

static void note_time(Time stamp) {
    if (stamp != CurrentTime) {
        selection_time = stamp;
    }
}

static void send_selection_notify(XSelectionRequestEvent *request, Atom property) {
    XSelectionEvent notify;

    memset(&notify, 0, sizeof notify);
    notify.type = SelectionNotify;
    notify.display = request->display;
    notify.requestor = request->requestor;
    notify.selection = request->selection;
    notify.target = request->target;
    notify.property = property;
    notify.time = request->time;
    XSendEvent(display, request->requestor, False, 0, (XEvent *)&notify);
}

static void send_png_property(Window requestor, Atom property, const unsigned char *data, size_t length) {
    XChangeProperty(display, requestor, property, atom_png, 8, PropModeReplace, data, (int)length);
}

static void begin_incr(XSelectionRequestEvent *request) {
    long remaining = (long)png_length;

    incr.active = 1;
    incr.generation = image_generation;
    incr.requestor = request->requestor;
    incr.property = request->property;
    incr.offset = 0;
    XSelectInput(display, request->requestor, PropertyChangeMask);
    XChangeProperty(display, request->requestor, request->property, atom_incr, 32,
                    PropModeReplace, (unsigned char *)&remaining, 1);
    send_selection_notify(request, request->property);
}

static void continue_incr(void) {
    size_t remaining;
    size_t chunk;

    if (!incr.active) {
        return;
    }
    remaining = png_length - incr.offset;
    if (remaining == 0) {
        XChangeProperty(display, incr.requestor, incr.property, atom_png, 8, PropModeReplace, NULL, 0);
        incr.active = 0;
        image_served = 1;
        note_status("served\n");
        return;
    }
    chunk = remaining > max_chunk_bytes ? max_chunk_bytes : remaining;
    send_png_property(incr.requestor, incr.property, png_bytes + incr.offset, chunk);
    incr.offset += chunk;
}

static void handle_selection_request(XSelectionRequestEvent *request) {
    Atom property = request->property == None ? request->target : request->property;

    note_time(request->time);
    if (request->selection != atom_clipboard || png_bytes == NULL) {
        send_selection_notify(request, None);
        return;
    }
    if (request->target == atom_targets) {
        Atom targets[4];

        targets[0] = atom_targets;
        targets[1] = atom_timestamp;
        targets[2] = atom_png;
        targets[3] = atom_incr;
        XChangeProperty(display, request->requestor, property, XA_ATOM, 32, PropModeReplace,
                        (unsigned char *)targets, 4);
        send_selection_notify(request, property);
        return;
    }
    if (request->target == atom_timestamp) {
        long stamp = (long)selection_time;
        XChangeProperty(display, request->requestor, property, XA_INTEGER, 32, PropModeReplace,
                        (unsigned char *)&stamp, 1);
        send_selection_notify(request, property);
        return;
    }
    if (request->target != atom_png) {
        send_selection_notify(request, None);
        return;
    }
    if (incr.active) {
        send_selection_notify(request, None);
        return;
    }
    if (png_length > max_chunk_bytes) {
        begin_incr(request);
        return;
    }
    send_png_property(request->requestor, property, png_bytes, png_length);
    send_selection_notify(request, property);
    image_served = 1;
    note_status("served\n");
}

static void handle_event(XEvent *event) {
    if (event->type == SelectionRequest) {
        handle_selection_request(&event->xselectionrequest);
        return;
    }
    if (event->type == SelectionClear) {
        note_time(event->xselectionclear.time);
        if (event->xselectionclear.selection == atom_clipboard) {
            selection_owned = 0;
            incr.active = 0;
        }
        return;
    }
    if (event->type == PropertyNotify) {
        note_time(event->xproperty.time);
        if (incr.active && event->xproperty.window == incr.requestor &&
            event->xproperty.atom == incr.property &&
            event->xproperty.state == PropertyDelete &&
            incr.generation == image_generation) {
            continue_incr();
        }
    }
}

static void drain_x_events(void) {
    while (XPending(display)) {
        XEvent event;
        XNextEvent(display, &event);
        handle_event(&event);
    }
}

static int claim_selection(void) {
    /* A stale timestamp is ignored once another client, including Xvnc's text
     * clipboard, has owned the selection more recently. CurrentTime is accepted. */
    XSetSelectionOwner(display, atom_clipboard, window, CurrentTime);
    XFlush(display);
    if (XGetSelectionOwner(display, atom_clipboard) != window) {
        selection_owned = 0;
        return -1;
    }
    selection_owned = 1;
    note_status("stored\n");
    return 0;
}

static int open_display(void) {
    int attempt;

    XSetErrorHandler(ignore_x_error);
    for (attempt = 0; attempt < 100 && !stop_requested; attempt++) {
        display = XOpenDisplay(NULL);
        if (display != NULL) {
            break;
        }
        nanosleep(&(struct timespec){ .tv_sec = 0, .tv_nsec = 100000000L }, NULL);
    }
    if (display == NULL) {
        fprintf(stderr, "cbc-clipboard-image: DISPLAY is not available\n");
        return -1;
    }
    atom_clipboard = XInternAtom(display, "CLIPBOARD", False);
    atom_targets = XInternAtom(display, "TARGETS", False);
    atom_png = XInternAtom(display, "image/png", False);
    atom_incr = XInternAtom(display, "INCR", False);
    atom_timestamp = XInternAtom(display, "TIMESTAMP", False);
    window = XCreateSimpleWindow(display, DefaultRootWindow(display), 0, 0, 1, 1, 0, 0, 0);
    XSelectInput(display, window, PropertyChangeMask);
    max_chunk_bytes = property_limit(display);
    {
        long dummy = 0;
        XChangeProperty(display, window, atom_timestamp, XA_INTEGER, 32, PropModeReplace,
                        (unsigned char *)&dummy, 1);
    }
    for (;;) {
        XEvent event;
        XNextEvent(display, &event);
        if (event.type == PropertyNotify && event.xproperty.window == window) {
            note_time(event.xproperty.time);
            break;
        }
        handle_event(&event);
    }
    return 0;
}

static int write_all(int fd, const char *data, size_t length) {
    size_t offset = 0;

    while (offset < length) {
        ssize_t written = write(fd, data + offset, length - offset);
        if (written < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        offset += (size_t)written;
    }
    return 0;
}

static int read_some(int fd, char *data, size_t capacity, size_t *used) {
    ssize_t count = read(fd, data + *used, capacity - *used);
    if (count < 0) {
        if (errno == EINTR) {
            return 0;
        }
        return -1;
    }
    if (count == 0) {
        return -1;
    }
    *used += (size_t)count;
    return 0;
}

static void respond(int fd, int status, const char *reason, const char *body) {
    char header[256];
    size_t body_length = body == NULL ? 0 : strlen(body);
    int header_length = snprintf(header, sizeof header,
                                 "HTTP/1.1 %d %s\r\nConnection: close\r\nContent-Type: text/plain\r\n"
                                 "Content-Length: %zu\r\n\r\n",
                                 status, reason, body_length);
    if (header_length < 0) {
        return;
    }
    write_all(fd, header, (size_t)header_length);
    if (body_length > 0) {
        write_all(fd, body, body_length);
    }
}

static int store_png(const unsigned char *data, size_t length) {
    unsigned char *copy;

    if (!png_signature(data, length) || length > MAX_IMAGE_BYTES) {
        return -1;
    }
    copy = malloc(length);
    if (copy == NULL) {
        return -1;
    }
    memcpy(copy, data, length);
    free(png_bytes);
    png_bytes = copy;
    png_length = length;
    image_served = 0;
    image_generation++;
    incr.active = 0;
    if (claim_selection() != 0) {
        return -1;
    }
    drain_x_events();
    XFlush(display);
    return 0;
}

static void handle_http(int fd) {
    char *header = malloc(MAX_HEADER_BYTES + 1);
    size_t used = 0;
    char *separator;
    unsigned long content_length = 0;
    int is_post = 0;
    int is_get = 0;

    if (header == NULL) {
        return;
    }
    while (used < MAX_HEADER_BYTES) {
        if (read_some(fd, header, MAX_HEADER_BYTES, &used) != 0) {
            free(header);
            return;
        }
        header[used] = '\0';
        separator = strstr(header, "\r\n\r\n");
        if (separator != NULL) {
            break;
        }
    }
    if (header[used] != '\0') {
        header[used] = '\0';
    }
    separator = strstr(header, "\r\n\r\n");
    if (separator == NULL) {
        respond(fd, 400, "Bad Request", "bad request\n");
        free(header);
        return;
    }
    is_post = strncmp(header, "POST ", 5) == 0;
    is_get = strncmp(header, "GET ", 4) == 0;
    if (is_get) {
        const char *state = !selection_owned ? "idle\n" : image_served ? "served\n" : "pending\n";
        respond(fd, 200, "OK", state);
        free(header);
        return;
    }
    if (!is_post) {
        respond(fd, 405, "Method Not Allowed", "method\n");
        free(header);
        return;
    }
    {
        char *length_header = strstr(header, "\r\nContent-Length:");
        char *end = NULL;
        if (length_header == NULL) {
            respond(fd, 411, "Length Required", "length\n");
            free(header);
            return;
        }
        content_length = strtoul(length_header + 17, &end, 10);
        if (end == length_header + 17 || content_length == 0 || content_length > MAX_IMAGE_BYTES) {
            respond(fd, 413, "Payload Too Large", "size\n");
            free(header);
            return;
        }
    }
    {
        size_t header_size = (size_t)(separator + 4 - header);
        size_t already = used - header_size;
        unsigned char *body = malloc(content_length);
        size_t got = 0;
        if (body == NULL) {
            respond(fd, 500, "Internal Server Error", "memory\n");
            free(header);
            return;
        }
        if (already > content_length) {
            already = content_length;
        }
        memcpy(body, separator + 4, already);
        got = already;
        while (got < content_length) {
            ssize_t count = read(fd, body + got, content_length - got);
            if (count < 0) {
                if (errno == EINTR) {
                    continue;
                }
                free(body);
                free(header);
                return;
            }
            if (count == 0) {
                free(body);
                respond(fd, 400, "Bad Request", "short body\n");
                free(header);
                return;
            }
            got += (size_t)count;
        }
        if (!png_signature(body, content_length)) {
            respond(fd, 415, "Unsupported Media Type", "png\n");
        } else if (store_png(body, content_length) != 0) {
            respond(fd, 500, "Internal Server Error", "selection\n");
        } else {
            respond(fd, 204, "No Content", NULL);
        }
        free(body);
    }
    free(header);
}

static int listen_socket(void) {
    struct sockaddr_un address;
    int fd;

    fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    memset(&address, 0, sizeof address);
    address.sun_family = AF_UNIX;
    strncpy(address.sun_path, SOCKET_PATH, sizeof address.sun_path - 1);
    unlink(SOCKET_PATH);
    if (bind(fd, (struct sockaddr *)&address, sizeof address) != 0 || listen(fd, 8) != 0) {
        close(fd);
        return -1;
    }
    chmod(SOCKET_PATH, 0600);
    return fd;
}

static int serve(void) {
    int server_fd = listen_socket();
    int x_fd;

    if (server_fd < 0) {
        fprintf(stderr, "cbc-clipboard-image: cannot listen on %s\n", SOCKET_PATH);
        return 1;
    }
    x_fd = ConnectionNumber(display);
    while (!stop_requested) {
        struct pollfd fds[2];
        int ready;

        drain_x_events();
        XFlush(display);
        fds[0].fd = x_fd;
        fds[0].events = POLLIN;
        fds[1].fd = server_fd;
        fds[1].events = POLLIN;
        ready = poll(fds, 2, 1000);
        if (ready < 0) {
            if (errno == EINTR) {
                continue;
            }
            break;
        }
        if (fds[0].revents & POLLIN) {
            drain_x_events();
        }
        if (fds[1].revents & POLLIN) {
            int client = accept(server_fd, NULL, NULL);
            struct timeval timeout;
            if (client < 0) {
                continue;
            }
            timeout.tv_sec = 5;
            timeout.tv_usec = 0;
            setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof timeout);
            handle_http(client);
            close(client);
        }
    }
    unlink(SOCKET_PATH);
    close(server_fd);
    return 0;
}

static int read_property(Window requestor, Atom property, unsigned char **out, unsigned long *out_length, Atom *out_type) {
    Atom actual_type = None;
    int actual_format = 0;
    unsigned long count = 0;
    unsigned long remaining = 0;
    unsigned char *data = NULL;

    if (XGetWindowProperty(display, requestor, property, 0, (~0L), True, AnyPropertyType,
                           &actual_type, &actual_format, &count, &remaining, &data) != Success) {
        return -1;
    }
    *out = data;
    *out_type = actual_type;
    if (actual_format == 8) {
        *out_length = count;
    } else if (actual_format == 16) {
        *out_length = count * 2;
    } else if (actual_format == 32) {
        *out_length = count * sizeof(long);
    } else {
        *out_length = 0;
    }
    return 0;
}

static int probe_selection(void) {
    unsigned char *collected = NULL;
    size_t collected_length = 0;
    int started = 0;
    int incr_mode = 0;

    XConvertSelection(display, atom_clipboard, atom_png, atom_png, window, selection_time);
    XFlush(display);
    while (!stop_requested) {
        XEvent event;
        XNextEvent(display, &event);
        if (event.type != SelectionNotify && event.type != PropertyNotify) {
            continue;
        }
        if (event.type == SelectionNotify) {
            unsigned char *data = NULL;
            unsigned long length = 0;
            Atom type = None;
            if (event.xselection.property == None) {
                free(collected);
                return 1;
            }
            if (read_property(window, atom_png, &data, &length, &type) != 0) {
                free(collected);
                return 1;
            }
            if (type == atom_incr) {
                incr_mode = 1;
                XFree(data);
                XFlush(display);
                continue;
            }
            {
                int valid = png_signature(data, length);
                int written = fwrite(data, 1, length, stdout) == length;
                XFree(data);
                free(collected);
                return valid && written ? 0 : 1;
            }
        }
        if (!incr_mode || event.xproperty.state != PropertyNewValue || event.xproperty.atom != atom_png) {
            continue;
        }
        {
            unsigned char *data = NULL;
            unsigned long length = 0;
            Atom type = None;
            unsigned char *next;
            if (read_property(window, atom_png, &data, &length, &type) != 0) {
                free(collected);
                return 1;
            }
            if (length == 0) {
                XFree(data);
                if (!started || fwrite(collected, 1, collected_length, stdout) != collected_length) {
                    free(collected);
                    return 1;
                }
                free(collected);
                return 0;
            }
            started = 1;
            next = realloc(collected, collected_length + length);
            if (next == NULL) {
                XFree(data);
                free(collected);
                return 1;
            }
            collected = next;
            memcpy(collected + collected_length, data, length);
            collected_length += length;
            XFree(data);
            XFlush(display);
        }
    }
    free(collected);
    return 1;
}

int main(int argc, char **argv) {
    int lock_fd;
    int probe = argc == 2 && strcmp(argv[1], "--probe") == 0;

    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, request_stop);
    signal(SIGINT, request_stop);
    if (!probe) {
        lock_fd = open(LOCK_PATH, O_CREAT | O_RDWR, 0600);
        if (lock_fd < 0 || flock(lock_fd, LOCK_EX | LOCK_NB) != 0) {
            fprintf(stderr, "cbc-clipboard-image: helper is already running\n");
            return 2;
        }
    }
    if (open_display() != 0) {
        return 1;
    }
    if (probe) {
        int status;
        alarm(5);
        status = probe_selection();
        XCloseDisplay(display);
        return status;
    }
    return serve();
}
