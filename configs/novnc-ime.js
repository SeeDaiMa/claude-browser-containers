// Desktop IME support for the pinned noVNC client. Keep ordinary keys and
// shortcuts in Keyboard. Transfer each committed phrase atomically through
// the existing UTF-8 clipboard protocol, then restore the previous clipboard.
// Host screenshots (WeChat, QQ, the OS, or any tool that puts an image on the
// clipboard) are posted to the container and pasted as image/png, then the
// previous text clipboard is restored.
import Keyboard from './keyboard.js';
import KeyTable from './keysym.js';
import { isTouchDevice } from '../util/browser.js';

const MAX_SCREENSHOT_BYTES = 20 * 1024 * 1024;

function imageFile(data) {
    if (!data) return null;
    const items = data.items ? Array.from(data.items) : [];
    for (const item of items) {
        if (item.kind === 'file' && item.type && item.type.startsWith('image/')) {
            const file = item.getAsFile();
            if (file) return file;
        }
    }
    const files = data.files ? Array.from(data.files) : [];
    return files.find((file) => file.type && file.type.startsWith('image/')) || null;
}

async function imageFromClipboardItems(items) {
    const preferred = ['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp'];
    for (const item of items) {
        const type = preferred.find((name) => item.types.includes(name))
            || item.types.find((name) => name.startsWith('image/') && name !== 'image/svg+xml');
        if (!type) continue;
        try {
            return await item.getType(type);
        } catch (error) {
            continue;
        }
    }
    return null;
}

async function toPngBlob(blob) {
    const header = new Uint8Array(await blob.slice(0, 8).arrayBuffer());
    const isPng = header.length === 8 && header[0] === 0x89 && header[1] === 0x50
        && header[2] === 0x4e && header[3] === 0x47;
    if (isPng) {
        return blob.type === 'image/png'
            ? blob
            : new Blob([await blob.arrayBuffer()], { type: 'image/png' });
    }
    const bitmap = await createImageBitmap(blob);
    try {
        const canvas = document.createElement('canvas');
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
        canvas.getContext('2d').drawImage(bitmap, 0, 0);
        const png = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
        if (!png) throw new Error('screenshot could not be converted');
        return png;
    } finally {
        bitmap.close();
    }
}

export default class IMEKeyboard extends Keyboard {
    constructor(canvas, rfb) {
        // The upstream UI already owns the touch keyboard's text input.
        const input = isTouchDevice ? canvas : document.createElement('textarea');
        super(input);
        this._canvas = canvas;
        this._input = input === canvas ? null : input;
        this._rfb = rfb;
        this._composing = false;
        this._inputQueue = [];
        this._paste = null;
        this._pasteTimer = null;
        this._keyCallback = null;
        this._remoteClipboard = '';
        this._clipboardEchoes = new Set();
        this._draining = false;
        this._hostPaste = null;
        this._imeHandlers = {
            focus: () => this._target.focus({ preventScroll: true }),
            position: (event) => {
                if (!this._paste) this._clipboardEchoes.clear();
                input.style.left = Math.max(0, Math.min(event.clientX, window.innerWidth - 24)) + 'px';
                input.style.top = Math.max(0, Math.min(event.clientY + 16, window.innerHeight - 24)) + 'px';
            },
            compositionstart: () => {
                this._allKeysUp();
                this._composing = true;
            },
            compositionend: (event) => {
                this._composing = false;
                this._sendText(event.data);
                input.value = '';
            },
            input: (event) => {
                if (this._composing || event.isComposing) return;
                this._sendText(input.value);
                input.value = '';
            },
            blur: () => {
                this._allKeysUp();
                this._composing = false;
                input.value = '';
            },
            paste: (event) => this._onHostPaste(event),
        };
        if (this._input) {
            input.dataset.cbcIme = '';
            input.setAttribute('aria-label', '远程桌面键盘输入');
            input.setAttribute('autocomplete', 'off');
            input.setAttribute('autocapitalize', 'off');
            input.spellcheck = false;
            input.tabIndex = -1;
            // Stay inside the viewport so the OS candidate window is visible.
            // Pointer events continue to reach the remote canvas underneath.
            input.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;' +
                'opacity:0;pointer-events:none;border:0;padding:0;resize:none;font-size:16px;';
        }
    }

    _handleKeyDown(event) {
        if ((event.ctrlKey || event.metaKey) && ['KeyC', 'KeyX'].includes(event.code)) {
            // An explicit user copy/cut may legitimately match an IME phrase.
            this._clipboardEchoes.clear();
        }
        if (this._input && (this._composing || event.isComposing || event.keyCode === 229)) {
            // Do not preventDefault: the host IME must receive candidate keys,
            // including the Enter/Space used to confirm a word.
            event.stopPropagation();
            return;
        }
        if (this._isHostPaste(event)) {
            // Leave the browser default in place so the paste event can see
            // images that clipboard.read() does not expose. The hidden field
            // still rejects the insertion in the paste listener.
            event.stopPropagation();
            if (event.repeat) return;
            this._enqueue({ hostPaste: this._armHostPaste() });
            return;
        }
        super._handleKeyDown(event);
    }

    _handleKeyUp(event) {
        if (this._input && (this._composing || event.isComposing || event.keyCode === 229)) {
            event.stopPropagation();
            return;
        }
        super._handleKeyUp(event);
    }

    _isHostPaste(event) {
        if (event.altKey) return false;
        if (event.code === 'KeyV' && (event.metaKey || event.ctrlKey)) return true;
        return event.code === 'Insert' && event.shiftKey && !event.metaKey && !event.ctrlKey;
    }

    _armHostPaste() {
        let resolve;
        const ticket = {
            mode: 'unknown',
            image: null,
            text: null,
            ready: new Promise((done) => {
                resolve = done;
            }),
        };
        ticket.finish = (mode) => {
            if (ticket.mode !== 'unknown') return;
            clearTimeout(ticket.timer);
            ticket.mode = mode;
            if (this._hostPaste === ticket) this._hostPaste = null;
            resolve(ticket);
        };
        this._hostPaste = ticket;
        // Start the read inside the key gesture. WeChat, QQ and OS screenshots
        // are normally image/png by the time the paste event arrives; read()
        // covers tools that only show up through the async clipboard.
        if (navigator.clipboard && navigator.clipboard.read) {
            navigator.clipboard.read().then(async (items) => {
                const image = await imageFromClipboardItems(items);
                if (image && ticket.mode === 'unknown') {
                    ticket.image = image;
                    ticket.finish('image');
                }
            }).catch(() => {});
        }
        ticket.timer = setTimeout(() => ticket.finish('text'), 150);
        return ticket;
    }

    _onHostPaste(event) {
        const ticket = this._hostPaste;
        if (!ticket || ticket.mode !== 'unknown') {
            if (this._input && event.target === this._input) event.preventDefault();
            return;
        }
        event.preventDefault();
        const file = imageFile(event.clipboardData);
        if (file) {
            ticket.image = file;
            ticket.finish('image');
            return;
        }
        const text = event.clipboardData ? event.clipboardData.getData('text/plain') : '';
        if (text) {
            ticket.text = text;
            ticket.finish('text');
        }
    }

    _sendText(text) {
        if (text) this._enqueue({ text });
    }

    _enqueue(event) {
        this._inputQueue.push(event);
        this._drain();
    }

    _drain() {
        if (this._draining) return;
        this._draining = true;
        void this._drainLoop();
    }

    async _drainLoop() {
        try {
            while (!this._paste && this._inputQueue.length) {
                const event = this._inputQueue.shift();
                if (event.keys) {
                    this._keyCallback(...event.keys);
                    continue;
                }
                if ('clipboard' in event) {
                    this._rfb.clipboardPasteFrom(event.clipboard);
                    continue;
                }
                if (event.hostPaste) {
                    await this._runHostPaste(event.hostPaste);
                    continue;
                }
                // Sending Unicode keysyms individually is lossy in this pinned
                // Xvnc/Chromium combination. Clipboard Provide transfers the whole
                // phrase without depending on temporary X11 keycode mappings.
                this._beginTextPaste(event.text, true);
            }
        } finally {
            this._draining = false;
            if (!this._paste && this._inputQueue.length) this._drain();
        }
    }

    _beginTextPaste(text, restorePrevious) {
        const previous = this._rfb._clipboardText ?? this._remoteClipboard ?? '';
        const paste = {
            text,
            previous: restorePrevious ? previous : text,
            keep: !restorePrevious,
        };
        this._clipboardEchoes.add(paste.text);
        this._clipboardEchoes.add(paste.previous);
        this._rfb.clipboardPasteFrom(text);
        this._paste = paste;
        this._sendPasteChord();
        // A non-editable target may never request clipboard data.
        this._pasteTimer = setTimeout(() => this._finishPaste(), 1000);
    }

    async _runHostPaste(ticket) {
        const result = await ticket.ready;
        try {
            if (result.mode === 'image' && result.image) {
                await this._pasteHostImage(result.image);
                return;
            }
            if (result.mode === 'text' && result.text) {
                const current = this._rfb._clipboardText ?? '';
                if (result.text !== current) {
                    this._beginTextPaste(result.text, false);
                    return;
                }
            }
            this._sendPasteChord();
        } catch (error) {
            console.error('截图粘贴失败', error);
            if (this._paste && this._paste.image) this._finishPaste();
        }
    }

    async skipHostClipboardSync(text) {
        // A screenshot has no text. Syncing that empty read would erase the
        // remote text clipboard before the picture is pasted.
        if (text) return false;
        if (!navigator.clipboard || !navigator.clipboard.read) return false;
        try {
            const items = await navigator.clipboard.read();
            return !!(await imageFromClipboardItems(items));
        } catch (error) {
            return false;
        }
    }

    async _pasteHostImage(blob) {
        const previous = this._rfb._clipboardText ?? this._remoteClipboard ?? '';
        this._clipboardEchoes.add(previous);
        this._paste = { image: true, previous };
        const png = await toPngBlob(blob);
        if (!png || png.size === 0 || png.size > MAX_SCREENSHOT_BYTES) {
            throw new Error('screenshot is empty or larger than 20 MB');
        }
        const response = await fetch('/cbc-clipboard-image', {
            method: 'POST',
            headers: { 'Content-Type': 'image/png' },
            body: png,
        });
        if (!response.ok) throw new Error('remote clipboard rejected the screenshot');
        this._sendPasteChord();
        const deadline = performance.now() + 2000;
        while (performance.now() < deadline) {
            const status = await fetch('/cbc-clipboard-image', { cache: 'no-store' });
            if (status.ok && (await status.text()).trim() === 'served') break;
            await new Promise((resolve) => setTimeout(resolve, 40));
        }
        // Chromium may issue a second image request after the first read.
        await new Promise((resolve) => setTimeout(resolve, 200));
        this._finishPaste();
    }

    _sendPasteChord() {
        const heldLeft = 'MetaLeft' in this._keyDownList || 'ControlLeft' in this._keyDownList;
        const heldRight = 'MetaRight' in this._keyDownList || 'ControlRight' in this._keyDownList;
        this._rfb.sendKey(KeyTable.XK_Control_L, 'ControlLeft', true);
        this._rfb.sendKey(0x76, 'KeyV', true);
        this._rfb.sendKey(0x76, 'KeyV', false);
        this._rfb.sendKey(KeyTable.XK_Control_L, 'ControlLeft', false);
        if (heldLeft) this._rfb.sendKey(KeyTable.XK_Control_L, 'ControlLeft', true);
        if (heldRight) this._rfb.sendKey(KeyTable.XK_Control_R, 'ControlRight', true);
    }

    clipboardProvided() {
        if (!this._paste || this._paste.image) return;
        if (this._paste && this._rfb._clipboardText === this._paste.text) {
            // The remote paste has requested and received its UTF-8 payload.
            // Give Chromium's asynchronous X11 paste time to consume it before
            // announcing the restored selection. Later key events stay queued.
            clearTimeout(this._pasteTimer);
            this._pasteTimer = setTimeout(() => this._finishPaste(), 150);
        }
    }

    _finishPaste() {
        clearTimeout(this._pasteTimer);
        this._pasteTimer = null;
        const paste = this._paste;
        this._paste = null;
        if (paste && paste.image) {
            this._remoteClipboard = paste.previous ?? '';
            this._clipboardEchoes.add(this._remoteClipboard);
            this._rfb.clipboardPasteFrom(this._remoteClipboard);
        } else if (paste && !paste.keep) {
            this._remoteClipboard = paste.previous;
            this._rfb.clipboardPasteFrom(paste.previous);
        } else if (paste && paste.keep) {
            this._remoteClipboard = paste.text;
        }
        this._drain();
    }

    deferClipboard(text) {
        if (!this._paste) return false;
        // An image-only host clipboard reads back as empty text. Do not let
        // that focus sync erase the text we are about to restore.
        if (this._paste.image && !text) return true;
        // A concurrent host focus/read must not replace an in-flight phrase.
        this._inputQueue.push({ clipboard: text });
        return true;
    }

    remoteClipboard(text) {
        // Xvnc may echo our temporary selection back to this client. It must
        // not replace the native pasteboard or the user's clipboard history.
        if (this._clipboardEchoes.has(text)) {
            // The upstream receiver clears _clipboardText before decoding a
            // server Provide. An echo must not erase the phrase still being
            // requested by Chromium, or a fast following paste can be empty.
            this._rfb._clipboardText = this._paste && !this._paste.image
                ? this._paste.text
                : this._remoteClipboard;
            return true;
        }
        this._remoteClipboard = text;
        if (this._paste && !this._paste.image) this._paste.previous = text;
        return false;
    }

    grab() {
        if (this._input) {
            if (!this._keyCallback) {
                this._keyCallback = this.onkeyevent;
                this.onkeyevent = (...keys) => this._enqueue({ keys });
            }
            this._canvas.parentNode.appendChild(this._input);
            for (const type of ['compositionstart', 'compositionend', 'input', 'blur', 'paste']) {
                this._input.addEventListener(type, this._imeHandlers[type]);
            }
            this._canvas.addEventListener('focus', this._imeHandlers.focus);
            this._canvas.addEventListener('mousedown', this._imeHandlers.position, true);
        }
        super.grab();
    }

    ungrab() {
        if (this._hostPaste) {
            this._hostPaste.finish('text');
            this._hostPaste = null;
        }
        if (this._input) {
            this._inputQueue = [];
            this._finishPaste();
            this._clipboardEchoes.clear();
            if (this._keyCallback) {
                this.onkeyevent = this._keyCallback;
                this._keyCallback = null;
            }
        }
        super.ungrab();
        if (this._input) {
            for (const type of ['compositionstart', 'compositionend', 'input', 'blur', 'paste']) {
                this._input.removeEventListener(type, this._imeHandlers[type]);
            }
            this._canvas.removeEventListener('focus', this._imeHandlers.focus);
            this._canvas.removeEventListener('mousedown', this._imeHandlers.position, true);
            this._input.remove();
            this._input.value = '';
            this._composing = false;
        }
    }
}
