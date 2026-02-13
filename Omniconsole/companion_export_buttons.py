import argparse
import json
import secrets
import re

from gma2telnet import GrandMA2Telnet


def _make_id(length=20):
    return secrets.token_urlsafe(16)[:length]


def _make_command_action(command, connection_id):
    return {
        "type": "action",
        "id": _make_id(),
        "definitionId": "command",
        "connectionId": connection_id,
        "options": {"command": command},
        "upgradeIndex": 0,
    }


def _make_toggle_variable_action(var_name):
    return {
        "type": "action",
        "id": _make_id(),
        "definitionId": "custom_variable_set_expression",
        "connectionId": "internal",
        "options": {
            "name": var_name,
            "expression": f"!$(custom:{var_name})",
        },
        "children": {},
    }


def _make_blink_feedback(var_name, connection_id, base_bgcolor=None):
    blink_bgcolor = 16711680
    if base_bgcolor == 0xFF0000:
        blink_bgcolor = 0x00FF00
    return {
        "type": "feedback",
        "id": _make_id(),
        "definitionId": "blinkVariable",
        "connectionId": connection_id,
        "options": {
            "info": "",
            "variable": f"$(custom:{var_name})",
            "op": "eq",
            "value": "true",
        },
        "upgradeIndex": 0,
        "isInverted": False,
        "style": {
            "color": 16777215,
            "bgcolor": blink_bgcolor,
        },
    }


def _make_button(
    text,
    exec_id,
    connection_id,
    mode="go",
    bgcolor=0,
    blink_connection_id=None,
    blink_var=None,
):
    if mode == "temp":
        down_actions = [
            _make_command_action(f"GO EXECUTOR {exec_id}", connection_id)
        ]
        up_actions = [
            _make_command_action(f"OFF EXECUTOR {exec_id}", connection_id)
        ]
        feedbacks = []
    else:
        down_actions = [
            _make_command_action(f"TOGGLE EXECUTOR {exec_id}", connection_id)
        ]
        up_actions = []
        feedbacks = []
        if blink_connection_id and blink_var:
            down_actions.append(_make_toggle_variable_action(blink_var))
            feedbacks.append(
                _make_blink_feedback(
                    blink_var, blink_connection_id, base_bgcolor=bgcolor
                )
            )
    return {
        "type": "button",
        "style": {
            "text": text,
            "textExpression": False,
            "size": "14",
            "png64": None,
            "alignment": "center:center",
            "pngalignment": "center:center",
            "color": 16777215,
            "bgcolor": bgcolor,
            "show_topbar": "default",
        },
        "options": {
            "stepProgression": "auto",
            "stepExpression": "",
            "rotaryActions": False,
        },
        "feedbacks": feedbacks,
        "steps": {
            "0": {
                "action_sets": {
                    "down": down_actions,
                    "up": up_actions,
                },
                "options": {"runWhileHeld": []},
            }
        },
        "localVariables": [],
    }


def _build_controls_from_grid(
    grid_cells, connection_id, keep_nav, blink_connection_id
):
    controls = {}
    if keep_nav:
        controls.setdefault("0", {})["0"] = {"type": "pageup"}
        controls.setdefault("1", {})["0"] = {"type": "pagenum"}
        controls.setdefault("2", {})["0"] = {"type": "pagedown"}

    for cell in grid_cells:
        if cell.get("nav"):
            continue
        exec_id = cell.get("exec_id")
        label = cell.get("label")
        if not exec_id or not label:
            continue
        bgcolor = cell.get("bgcolor")
        if bgcolor is None:
            bgcolor = 0
        row = cell["row"]
        col = cell["col"]
        blink_var = _blink_var_for_exec(exec_id)
        controls.setdefault(str(row), {})[str(col)] = _make_button(
            label,
            exec_id,
            connection_id,
            mode=cell.get("mode", "go"),
            bgcolor=bgcolor,
            blink_connection_id=blink_connection_id,
            blink_var=blink_var,
        )
    return controls


def _iter_connection_maps(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in ("connections", "instances") and isinstance(value, dict):
                yield value
            else:
                yield from _iter_connection_maps(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_connection_maps(item)


def _is_grandma_connection(conn):
    try:
        text = json.dumps(conn).lower()
    except TypeError:
        text = str(conn).lower()
    return any(token in text for token in ("grandma", "grandma2", "gma2", "ma2"))


def _find_grandma_connection(config):
    candidates = []
    for conn_map in _iter_connection_maps(config):
        for conn_id, conn in conn_map.items():
            if not isinstance(conn, dict):
                continue
            if _is_grandma_connection(conn) or any(
                token in str(conn_id).lower()
                for token in ("grandma", "grandma2", "gma2", "ma2")
            ):
                label = conn.get("label") or conn.get("name") or ""
                candidates.append((conn_id, label, conn))
    if not candidates:
        raise SystemExit(
            "Could not find a GrandMA2 connection in the config. "
            "Pass a config with a GrandMA2 connection."
        )
    if len(candidates) > 1:
        details = ", ".join(f"{cid}({label})" for cid, label, _ in candidates)
        raise SystemExit(
            "Multiple GrandMA2 connections found, unable to choose: " + details
        )
    conn_id, _label, conn = candidates[0]
    return conn_id, conn


def _iter_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_dicts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_dicts(item)


def _extract_host_port(conn):
    host = None
    port = None
    for data in _iter_dicts(conn):
        for key, value in data.items():
            key_lower = str(key).lower()
            if key_lower in ("host", "hostname", "ip", "address"):
                if isinstance(value, (str, int)):
                    host = str(value)
            if key_lower == "port":
                if isinstance(value, (str, int)):
                    try:
                        port = int(value)
                    except ValueError:
                        continue
    return host, port


def _extract_login(conn):
    user = None
    password = None
    for data in _iter_dicts(conn):
        for key, value in data.items():
            key_lower = str(key).lower()
            if key_lower in ("user", "username", "login"):
                if isinstance(value, (str, int)):
                    user = str(value)
            if key_lower in ("password", "pass", "pwd"):
                if isinstance(value, (str, int)):
                    password = str(value)
    return user, password


def _get_pages_container(config):
    if isinstance(config, dict):
        if "pages" in config and isinstance(config["pages"], dict):
            return config["pages"]
        if "page" in config and isinstance(config["page"], dict):
            return config["page"]
        if "pagesV2" in config and isinstance(config["pagesV2"], dict):
            return config["pagesV2"]
        if all(
            isinstance(value, dict) and "controls" in value
            for value in config.values()
        ):
            return config
    raise SystemExit("Could not locate pages container in the config.")


def _next_page_index(pages):
    numeric = []
    for key in pages.keys():
        try:
            numeric.append(int(key))
        except (TypeError, ValueError):
            continue
    return max(numeric, default=0) + 1


def _compact_pages_container(pages):
    numeric_keys = []
    non_numeric = {}
    for key, value in pages.items():
        if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
            numeric_keys.append((int(key), key, value))
        else:
            non_numeric[key] = value
    if not numeric_keys:
        return {}
    numeric_keys.sort(key=lambda item: item[0])
    new_pages = {}
    mapping = {}
    for idx, (_num, _key, value) in enumerate(numeric_keys, start=1):
        new_key = str(idx)
        new_pages[new_key] = value
        mapping[str(_key)] = new_key
    new_pages.update(non_numeric)
    pages.clear()
    pages.update(new_pages)
    return mapping


def _sync_page_lists(config, pages_container, mapping=None):
    valid_keys = {
        str(key)
        for key in pages_container.keys()
        if isinstance(key, int) or (isinstance(key, str) and key.isdigit())
    }
    ordered_keys = sorted(valid_keys, key=lambda k: int(k))
    page_ids_by_key = {}
    for key, page in pages_container.items():
        if not (isinstance(key, int) or (isinstance(key, str) and str(key).isdigit())):
            continue
        if isinstance(page, dict):
            page_id = page.get("id")
            if page_id:
                page_ids_by_key[str(key)] = str(page_id)
    page_ids = set(page_ids_by_key.values())
    ordered_page_ids = [page_ids_by_key[k] for k in ordered_keys if k in page_ids_by_key]

    def normalize_list(values):
        if not values:
            return values
        if any(isinstance(item, (dict, list)) for item in values):
            return values
        prefer_ints = all(isinstance(item, int) for item in values)
        str_items = [str(item) for item in values]
        if any(item in page_ids for item in str_items):
            updated = [item for item in str_items if item in page_ids]
            return updated
        if all(item.isdigit() for item in str_items):
            updated = []
            for item in str_items:
                key = mapping.get(item, item) if mapping else item
                if key in valid_keys:
                    updated.append(key)
            if prefer_ints:
                return [int(key) for key in updated]
            return updated
        return values

    order_keys = {"pageorder", "pagesorder", "page_order", "pages_order"}

    def walk(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_lower = str(key).lower()
                if isinstance(value, list) and "page" in key_lower:
                    normalized = normalize_list(value)
                    if key_lower in order_keys:
                        if normalized and any(item in page_ids for item in normalized):
                            obj[key] = ordered_page_ids
                        else:
                            obj[key] = (
                                [int(k) for k in ordered_keys]
                                if all(isinstance(item, int) for item in value)
                                else ordered_keys
                            )
                    else:
                        obj[key] = normalized
                else:
                    walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(config)


def _find_custom_var_container(config):
    keys = ("customVariables", "custom_variables")
    for key in keys:
        if isinstance(config, dict) and key in config:
            return config, key
    stack = [config]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key in keys:
                    return current, key
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            for item in current:
                if isinstance(item, (dict, list)):
                    stack.append(item)
    return None, None


def _ensure_custom_variable(config, var_name, default_value="false"):
    parent, key = _find_custom_var_container(config)
    if parent is None:
        config["customVariables"] = {var_name: default_value}
        return
    container = parent.get(key)
    if isinstance(container, dict):
        container.setdefault(var_name, default_value)
        return
    if isinstance(container, list):
        if any(isinstance(item, dict) and "name" in item for item in container):
            if not any(
                isinstance(item, dict) and item.get("name") == var_name
                for item in container
            ):
                container.append({"name": var_name, "value": default_value})
        else:
            if var_name not in container:
                container.append(var_name)
        return
    parent[key] = {var_name: default_value}


def _blink_var_for_exec(exec_id):
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(exec_id))
    return f"ma_exec_{safe}".lower()


def _find_blink_connection_id(config):
    ids = set()
    for data in _iter_dicts(config):
        if isinstance(data, dict) and data.get("definitionId") == "blinkVariable":
            conn_id = data.get("connectionId")
            if conn_id:
                ids.add(conn_id)
    if len(ids) == 1:
        return next(iter(ids))
    if len(ids) > 1:
        raise SystemExit(
            "Multiple blinkVariable feedback connection IDs found: "
            + ", ".join(sorted(ids))
        )

    candidates = []
    for conn_map in _iter_connection_maps(config):
        for conn_id, conn in conn_map.items():
            if not isinstance(conn, dict):
                continue
            blob = json.dumps(conn).lower()
            if "generic" in blob or "blink" in blob:
                candidates.append(conn_id)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise SystemExit(
            "Multiple possible generic/blink connections found. "
            "Please pass --blink-connection-id."
        )
    return None


def _fetch_button_execs_all_pages(
    gma2,
    max_pages=10,
    left_start=101,
    left_end=190,
    xkey_start=201,
    xkey_end=220,
):
    grouped = {}
    for page in range(1, max_pages + 1):
        gma2.list_executor_range(page, left_start, left_end)
        for exec_id in range(left_start, left_end + 1):
            label = gma2.execIdToName.get((page, exec_id), "")
            label = (label or "").strip()
            if not label or label.lower() == "exec":
                continue
            grouped.setdefault(page, []).append((exec_id, label))

        gma2.list_executor_range(page, xkey_start, xkey_end)
        for exec_id in range(xkey_start, xkey_end + 1):
            label = gma2.execIdToName.get((page, exec_id), "")
            label = (label or "").strip()
            if not label or label.lower() == "exec":
                continue
            grouped.setdefault(page, []).append((exec_id, label))

    for page in grouped:
        grouped[page].sort(key=lambda item: item[0])
    return grouped


def _extract_exec_id_from_control(control):
    if not isinstance(control, dict):
        return None
    steps = control.get("steps", {})
    step0 = steps.get("0", {})
    action_sets = step0.get("action_sets", {})
    down = action_sets.get("down", [])
    up = action_sets.get("up", [])
    for action in down + up:
        if not isinstance(action, dict):
            continue
        cmd = action.get("options", {}).get("command", "")
        if not cmd:
            continue
        cmd_upper = cmd.upper()
        if "EXECUTOR" in cmd_upper:
            parts = cmd_upper.split()
            for idx, part in enumerate(parts):
                if part == "EXECUTOR" and idx + 1 < len(parts):
                    return _normalize_exec_id(parts[idx + 1])
    return None


def _collect_mapped_execs(pages_container):
    mapped = {}
    for page_key, page_obj in pages_container.items():
        if not isinstance(page_obj, dict):
            continue
        controls = page_obj.get("controls", {})
        if not isinstance(controls, dict):
            continue
        for row in controls.values():
            if not isinstance(row, dict):
                continue
            for control in row.values():
                if not isinstance(control, dict):
                    continue
                if control.get("type") != "button":
                    continue
                exec_id = _extract_exec_id_from_control(control)
                if not exec_id:
                    continue
                mapped.setdefault(exec_id, set()).add(str(page_key))
    return mapped


def _normalize_exec_id(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.rstrip(";,")
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    return match.group(1)


def _run_gui(
    args,
    config,
    connection_id,
    blink_connection_id,
    host,
    port,
    user,
    password,
):
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog

    gma2 = GrandMA2Telnet(
        host=host,
        port=port,
        user=user,
        password=password,
    )
    gma2.connect()
    pages_container = _get_pages_container(config)
    mapped_execs = _collect_mapped_execs(pages_container)
    left_start = 101
    left_end = 190
    grouped = _fetch_button_execs_all_pages(
        gma2,
        left_start=left_start,
        left_end=left_end,
    )
    if not grouped:
        messagebox.showerror(
            "GrandMA2",
            "No button executors found for the requested range.",
        )
        return

    root = tk.Tk()
    root.title("Companion Button Import")
    root.geometry("1500x900")

    left_frame = ttk.Frame(root, padding=8)
    left_frame.pack(side="left", fill="y")

    right_frame = ttk.Frame(root, padding=8)
    right_frame.pack(side="right", fill="both", expand=True)

    execs_frame = ttk.Frame(left_frame)
    xkey_frame = ttk.Frame(left_frame)
    execs_frame.grid(row=0, column=0, sticky="nsew")
    xkey_frame.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
    left_frame.rowconfigure(0, weight=1)
    left_frame.columnconfigure(0, weight=0)
    left_frame.columnconfigure(1, weight=0)

    ttk.Label(execs_frame, text=f"Executors ({left_start}-{left_end})").grid(
        row=0, column=0, columnspan=2, sticky="w"
    )
    exec_tree = ttk.Treeview(
        execs_frame, columns=("label",), show="tree", selectmode="extended"
    )
    exec_scroll = ttk.Scrollbar(execs_frame, orient="vertical")
    exec_tree.configure(yscrollcommand=exec_scroll.set)
    exec_scroll.configure(command=exec_tree.yview)
    exec_scroll.grid(row=1, column=0, sticky="ns")
    exec_tree.grid(row=1, column=1, sticky="nsew")
    execs_frame.rowconfigure(1, weight=1)
    execs_frame.columnconfigure(1, weight=1)

    ttk.Label(xkey_frame, text="X-Key (201-220)").grid(
        row=0, column=0, columnspan=2, sticky="w"
    )
    xkey_tree = ttk.Treeview(
        xkey_frame, columns=("label",), show="tree", selectmode="extended"
    )
    xkey_scroll = ttk.Scrollbar(xkey_frame, orient="vertical")
    xkey_tree.configure(yscrollcommand=xkey_scroll.set)
    xkey_scroll.configure(command=xkey_tree.yview)
    xkey_scroll.grid(row=1, column=0, sticky="ns")
    xkey_tree.grid(row=1, column=1, sticky="nsew")
    xkey_frame.rowconfigure(1, weight=1)
    xkey_frame.columnconfigure(1, weight=1)

    tree_items_by_tree = {exec_tree: {}, xkey_tree: {}}

    from tkinter import font as tkfont
    style = ttk.Style()
    tree_font = style.lookup("Treeview", "font")
    if tree_font:
        default_font = tkfont.nametofont(tree_font)
    else:
        default_font = tkfont.nametofont("TkDefaultFont")
    exec_tree_max_width = 0
    xkey_tree_max_width = 0

    def _format_page_list(page_list):
        def sort_key(value):
            return (0, int(value)) if str(value).isdigit() else (1, str(value))

        return "/".join(sorted(page_list, key=sort_key))

    def _display_text(exec_id, label, page_list):
        base = f"{exec_id}: {label}" if label else f"{exec_id}:"
        if page_list:
            base = f"{base} ({_format_page_list(page_list)})"
        return base

    exec_tree.tag_configure("mapped", foreground="#158a17")
    xkey_tree.tag_configure("mapped", foreground="#158a17")

    for page in range(1, 11):
        page_text = f"Page {page}"
        exec_tree_max_width = max(exec_tree_max_width, default_font.measure(page_text))
        xkey_tree_max_width = max(xkey_tree_max_width, default_font.measure(page_text))
        page_id = exec_tree.insert("", "end", text=page_text, open=True)
        for exec_id, label in grouped.get(page, []):
            if 201 <= exec_id <= 220:
                continue
            label = (label or "").strip()
            if label.lower() == "exec":
                label = ""
            exec_key = f"{page}.{exec_id}"
            page_list = mapped_execs.get(exec_key, set()) or mapped_execs.get(
                str(exec_id), set()
            )
            text = _display_text(exec_id, label, page_list)
            label_only = label
            exec_tree_max_width = max(exec_tree_max_width, default_font.measure(text))
            item_id = exec_tree.insert(
                page_id,
                "end",
                text=text,
                tags=("mapped",) if page_list else (),
            )
            tree_items_by_tree[exec_tree][item_id] = {
                "page": page,
                "exec_id": exec_id,
                "label": label_only,
            }

        page_id = xkey_tree.insert("", "end", text=page_text, open=True)
        for exec_id, label in grouped.get(page, []):
            if exec_id < 201 or exec_id > 220:
                continue
            label = (label or "").strip()
            if label.lower() == "exec":
                label = ""
            exec_key = f"{page}.{exec_id}"
            page_list = mapped_execs.get(exec_key, set()) or mapped_execs.get(
                str(exec_id), set()
            )
            text = _display_text(exec_id, label, page_list)
            label_only = label
            xkey_tree_max_width = max(xkey_tree_max_width, default_font.measure(text))
            item_id = xkey_tree.insert(
                page_id,
                "end",
                text=text,
                tags=("mapped",) if page_list else (),
            )
            tree_items_by_tree[xkey_tree][item_id] = {
                "page": page,
                "exec_id": exec_id,
                "label": label_only,
            }

    indent = style.lookup("Treeview", "indent")
    try:
        indent_px = int(indent)
    except (TypeError, ValueError):
        indent_px = 20
    exec_width = exec_tree_max_width + indent_px + 32
    xkey_width = xkey_tree_max_width + indent_px + 32
    def _apply_tree_widths(exec_width_px, xkey_width_px):
        exec_tree.column("#0", width=exec_width_px, minwidth=exec_width_px, stretch=False)
        xkey_tree.column("#0", width=xkey_width_px, minwidth=xkey_width_px, stretch=False)
        execs_frame.configure(width=exec_width_px + 20)
        xkey_frame.configure(width=xkey_width_px + 20)
        execs_frame.grid_propagate(False)
        xkey_frame.grid_propagate(False)
        left_frame.configure(width=exec_width_px + xkey_width_px + 60)
        left_frame.grid_propagate(False)

    def _refresh_tree_mappings():
        nonlocal exec_tree_max_width, xkey_tree_max_width, mapped_execs
        mapped_execs = _collect_mapped_execs(pages_container)
        exec_tree_max_width = 0
        xkey_tree_max_width = 0
        for tree_widget in (exec_tree, xkey_tree):
            for item_id, data in tree_items_by_tree[tree_widget].items():
                exec_key = f"{data['page']}.{data['exec_id']}"
                page_list = mapped_execs.get(exec_key, set())
                text = _display_text(data["exec_id"], data["label"], page_list)
                tree_widget.item(
                    item_id,
                    text=text,
                    tags=("mapped",) if page_list else (),
                )
                width = default_font.measure(text)
                if tree_widget is exec_tree:
                    exec_tree_max_width = max(exec_tree_max_width, width)
                else:
                    xkey_tree_max_width = max(xkey_tree_max_width, width)
        exec_width_px = exec_tree_max_width + indent_px + 32
        xkey_width_px = xkey_tree_max_width + indent_px + 32
        _apply_tree_widths(exec_width_px, xkey_width_px)

    _apply_tree_widths(exec_width, xkey_width)

    page_select_frame = ttk.Frame(right_frame)
    page_select_frame.pack(fill="x", pady=(0, 6))
    ttk.Label(page_select_frame, text="Edit page").pack(side="left")
    page_select_var = tk.StringVar(value="new")
    page_choices = ["new"]
    for key in sorted(pages_container.keys(), key=lambda k: int(k)):
        page_obj = pages_container.get(key, {})
        name = page_obj.get("name") or f"Page {key}"
        page_choices.append(f"{key}: {name}")
    page_select = ttk.Combobox(
        page_select_frame,
        textvariable=page_select_var,
        values=page_choices,
        state="readonly",
        width=28,
    )
    page_select.pack(side="left", padx=6)

    name_frame = ttk.Frame(right_frame)
    name_frame.pack(fill="x", pady=(0, 6))
    ttk.Label(name_frame, text="Page name").pack(side="left")
    page_name_var = tk.StringVar(value="import")
    ttk.Entry(name_frame, textvariable=page_name_var, width=24).pack(
        side="left", padx=6
    )

    ttk.Label(right_frame, text="Streamdeck XL (8x4)").pack(anchor="w")
    streamdeck_width_cm = 20.0
    streamdeck_height_cm = 10.0
    dpi = root.winfo_fpixels("1i")
    streamdeck_width = int(streamdeck_width_cm / 2.54 * dpi)
    streamdeck_height = int(streamdeck_height_cm / 2.54 * dpi)
    grid_bg_photo = {"image": None}
    streamdeck_image_path = "background.png"
    if streamdeck_image_path:
        try:
            from PIL import Image, ImageTk  # type: ignore

            image = Image.open(streamdeck_image_path)
            image = image.resize(
                (streamdeck_width, streamdeck_height),
                Image.LANCZOS,
            )
            grid_bg_photo["image"] = ImageTk.PhotoImage(image)
        except Exception:
            try:
                grid_bg_photo["image"] = tk.PhotoImage(file=streamdeck_image_path)
                if (
                    grid_bg_photo["image"].width() != streamdeck_width
                    or grid_bg_photo["image"].height() != streamdeck_height
                ):
                    print(
                        "Warning: streamdeck image not scaled to 20cm x 10cm. "
                        "Install Pillow for proper scaling."
                    )
            except Exception as exc:
                messagebox.showwarning(
                    "Streamdeck image",
                    f"Unable to load image: {exc}",
                )

    grid_canvas = tk.Canvas(
        right_frame,
        width=streamdeck_width,
        height=streamdeck_height,
        highlightthickness=0,
    )
    grid_canvas.pack(anchor="w")

    keep_nav = tk.BooleanVar(value=not args.no_nav)
    nav_frame = ttk.Frame(right_frame)
    nav_frame.pack(fill="x", pady=(6, 0))
    ttk.Checkbutton(
        nav_frame,
        text="Keep nav buttons (page up/num/down)",
        variable=keep_nav,
    ).pack(anchor="w")

    grid_cells = []
    cell_by_widget = {}
    mode_labels = {"go": "go", "temp": "temp"}
    mode_display = {"go": "go (latched)", "temp": "temp"}
    color_options = [
        ("Default", None, None),
        ("Red", 0xFF0000, "#ff0000"),
        ("Green", 0x00FF00, "#00ff00"),
        ("Blue", 0x0000FF, "#0000ff"),
        ("Yellow", 0xFFFF00, "#ffff00"),
        ("Orange", 0xFFA500, "#ffa500"),
        ("Purple", 0x800080, "#800080"),
        ("Cyan", 0x00FFFF, "#00ffff"),
        ("Magenta", 0xFF00FF, "#ff00ff"),
        ("White", 0xFFFFFF, "#ffffff"),
        ("Gray", 0x808080, "#808080"),
    ]
    color_hex_by_value = {
        value: hex_value for _name, value, hex_value in color_options if value is not None
    }
    selected_color = {"value": None, "hex": None, "name": "Default"}
    palette_buttons = []

    palette_frame = tk.Frame(right_frame)
    palette_frame.pack(fill="x", pady=(0, 6), before=grid_canvas)
    ttk.Label(palette_frame, text="Palette").pack(side="left")

    def select_palette_color(value, color_hex, name, button_widget):
        selected_color["value"] = value
        selected_color["hex"] = color_hex
        selected_color["name"] = name
        for btn in palette_buttons:
            btn.configure(relief="raised")
        button_widget.configure(relief="sunken")

    for name, color_value, color_hex in color_options:
        btn = tk.Button(
            palette_frame,
            text=name if color_hex is None else "",
            width=8,
            relief="raised",
            bg=color_hex or palette_frame.cget("bg"),
        )
        btn.pack(side="left", padx=2)
        btn.configure(
            command=lambda v=color_value, h=color_hex, n=name, b=btn: select_palette_color(
                v, h, n, b
            )
        )
        palette_buttons.append(btn)
        if name == "Default":
            btn.configure(relief="sunken")

    current_page_key = {"value": None}
    nav_dirty = {"value": False}
    loading_page = {"value": False}

    def cell_text(cell):
        if cell.get("nav"):
            return cell["nav_label"]
        if not cell.get("label"):
            return ""
        return f"{cell['label']}\n[{mode_labels.get(cell['mode'], cell['mode'])}]"

    def update_cell_button(cell):
        button = cell["button"]
        button.configure(text=cell_text(cell))
        if cell.get("bg_hex"):
            button.configure(bg=cell["bg_hex"], activebackground=cell["bg_hex"])
        else:
            button.configure(
                bg=cell.get("default_bg", button.cget("bg")),
                activebackground=cell.get("default_bg", button.cget("bg")),
            )

    def init_grid():
        grid_canvas.delete("all")
        if grid_bg_photo["image"] is not None:
            grid_canvas.create_image(0, 0, anchor="nw", image=grid_bg_photo["image"])
        grid_cells.clear()
        cell_by_widget.clear()

        cell_width = streamdeck_width / 8.0
        cell_height = streamdeck_height / 4.0
        pad = max(4, int(min(cell_width, cell_height) * 0.06))
        btn_width = int(cell_width - pad * 2)
        btn_height = int(cell_height - pad * 2)
        wrap_length = max(60, int(btn_width * 0.9))

        for row in range(4):
            for col in range(8):
                is_nav = keep_nav.get() and (col, row) in {(0, 0), (0, 1), (0, 2)}
                nav_label = None
                if is_nav:
                    nav_label = (
                        "Page Up"
                        if row == 0
                        else "Page #"
                        if row == 1
                        else "Page Down"
                    )
                btn = tk.Button(
                    grid_canvas,
                    text=nav_label or "",
                    relief="ridge",
                    state="disabled" if is_nav else "normal",
                    wraplength=wrap_length,
                )
                x = int(col * cell_width + pad)
                y = int(row * cell_height + pad)
                grid_canvas.create_window(
                    x,
                    y,
                    anchor="nw",
                    width=btn_width,
                    height=btn_height,
                    window=btn,
                )
                cell = {
                    "row": row,
                    "col": col,
                    "button": btn,
                    "exec_id": None,
                    "label": None,
                    "mode": "go",
                    "bgcolor": None,
                    "bg_hex": None,
                    "default_bg": btn.cget("bg"),
                    "nav": is_nav,
                    "nav_label": nav_label,
                    "dirty": False,
                    "original_sig": None,
                }
                grid_cells.append(cell)
                cell_by_widget[btn] = cell
                if not is_nav:
                    btn.bind("<Button-3>", lambda e, c=cell: open_context_menu(c, e))
                    btn.bind("<Button-1>", lambda e, c=cell: on_grid_press(c, e))
                    btn.bind("<B1-Motion>", on_grid_motion)
                    btn.bind("<ButtonRelease-1>", lambda e, c=cell: on_grid_release(c, e))
                update_cell_button(cell)

    def open_context_menu(cell, event):
        if cell.get("nav") or not cell.get("exec_id"):
            return
        menu = tk.Menu(root, tearoff=0)
        menu.add_command(
            label="Mode: go (latched)",
            command=lambda: _set_mode(cell, "go"),
        )
        menu.add_command(
            label="Mode: temp",
            command=lambda: _set_mode(cell, "temp"),
        )

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _set_mode(cell, mode):
        if cell.get("mode") == mode:
            return
        cell["mode"] = mode
        _mark_dirty(cell)
        update_cell_button(cell)

    def _set_color(cell, color_value, color_hex):
        if cell.get("bgcolor") == color_value and cell.get("bg_hex") == color_hex:
            return
        cell["bgcolor"] = color_value
        cell["bg_hex"] = color_hex
        _mark_dirty(cell)
        update_cell_button(cell)

    def on_grid_left_click(cell):
        if cell.get("nav") or not cell.get("exec_id"):
            return
        _set_color(cell, selected_color["value"], selected_color["hex"])

    grid_drag = {
        "cell": None,
        "dragging": False,
        "widget": None,
        "start_x": 0,
        "start_y": 0,
    }

    def _clear_cell(cell):
        cell["exec_id"] = None
        cell["label"] = None
        cell["mode"] = "go"
        cell["bgcolor"] = None
        cell["bg_hex"] = None
        _mark_dirty(cell)
        update_cell_button(cell)

    def _move_exec(source, target):
        if source is target:
            return
        target["exec_id"] = source.get("exec_id")
        target["label"] = source.get("label")
        target["mode"] = source.get("mode", "go")
        target["bgcolor"] = source.get("bgcolor")
        target["bg_hex"] = source.get("bg_hex")
        _mark_dirty(target)
        update_cell_button(target)
        _clear_cell(source)

    def on_grid_press(cell, event):
        if cell.get("nav") or not cell.get("exec_id"):
            return
        grid_drag["cell"] = cell
        grid_drag["dragging"] = False
        grid_drag["start_x"] = event.x_root
        grid_drag["start_y"] = event.y_root

    def on_grid_motion(_event):
        if not grid_drag.get("cell"):
            return
        if not grid_drag["dragging"]:
            dx = abs(root.winfo_pointerx() - grid_drag["start_x"])
            dy = abs(root.winfo_pointery() - grid_drag["start_y"])
            if dx < 4 and dy < 4:
                return
            grid_drag["dragging"] = True
            drag_label = tk.Toplevel(root)
            drag_label.overrideredirect(True)
            label_text = grid_drag["cell"].get("label") or ""
            ttk.Label(drag_label, text=label_text).pack()
            drag_label.lift()
            grid_drag["widget"] = drag_label
        if grid_drag.get("widget"):
            x = root.winfo_pointerx() + 10
            y = root.winfo_pointery() + 10
            grid_drag["widget"].geometry(f"+{x}+{y}")

    def on_grid_release(cell, _event):
        if not grid_drag.get("cell"):
            return
        drag_widget = grid_drag.get("widget")
        if drag_widget:
            drag_widget.destroy()
        source = grid_drag["cell"]
        dragging = grid_drag["dragging"]
        grid_drag.update({"cell": None, "dragging": False, "widget": None})
        if dragging:
            widget = root.winfo_containing(root.winfo_pointerx(), root.winfo_pointery())
            if widget in cell_by_widget:
                target = cell_by_widget[widget]
                if target.get("nav"):
                    _clear_cell(source)
                else:
                    _move_exec(source, target)
            else:
                _clear_cell(source)
            return
        if source is cell:
            on_grid_left_click(cell)

    def _cell_signature(cell):
        return (
            cell.get("exec_id"),
            cell.get("mode"),
            cell.get("bgcolor"),
            cell.get("label"),
        )

    def _mark_dirty(cell):
        if cell.get("original_sig") is None:
            cell["dirty"] = True
        else:
            cell["dirty"] = _cell_signature(cell) != cell["original_sig"]

    def assign_exec_to_cell(cell, exec_data):
        if cell.get("nav"):
            return
        cell["exec_id"] = f"{exec_data['page']}.{exec_data['exec_id']}"
        cell["label"] = exec_data["label"]
        cell["mode"] = cell.get("mode") or "go"
        _mark_dirty(cell)
        update_cell_button(cell)

    drag_data = {"item": None, "drag_widget": None, "exec_data": None, "exec_list": []}

    def _selected_execs(tree_widget):
        selected = set(tree_widget.selection())
        if not selected:
            return []
        exec_list = []
        parent = tree_widget.get_children("")
        for page_item in parent:
            for item in tree_widget.get_children(page_item):
                if item in selected and item in tree_items_by_tree[tree_widget]:
                    exec_list.append(tree_items_by_tree[tree_widget][item])
        return exec_list

    def on_tree_press(tree_widget, event):
        item = tree_widget.identify_row(event.y)
        if not item or item not in tree_items_by_tree[tree_widget]:
            return
        if event.state & 0x0005:
            return
        if item not in tree_widget.selection():
            tree_widget.selection_set(item)
        drag_data["item"] = item
        drag_data["exec_list"] = _selected_execs(tree_widget) or [
            tree_items_by_tree[tree_widget][item]
        ]
        drag_data["exec_data"] = (
            drag_data["exec_list"][0] if drag_data["exec_list"] else None
        )
        drag_label = tk.Toplevel(root)
        drag_label.overrideredirect(True)
        label_text = tree_items_by_tree[tree_widget][item]["label"]
        if len(drag_data["exec_list"]) > 1:
            label_text = f"{label_text} (+{len(drag_data['exec_list']) - 1})"
        ttk.Label(drag_label, text=label_text).pack()
        drag_label.lift()
        drag_data["drag_widget"] = drag_label

    def on_tree_motion(_event):
        if not drag_data.get("drag_widget"):
            return
        x = root.winfo_pointerx() + 10
        y = root.winfo_pointery() + 10
        drag_data["drag_widget"].geometry(f"+{x}+{y}")

    def on_tree_release(_event):
        drag_widget = drag_data.get("drag_widget")
        if drag_widget:
            drag_widget.destroy()
        drag_data["drag_widget"] = None

        exec_list = drag_data.get("exec_list") or []
        drag_data["item"] = None
        drag_data["exec_data"] = None
        drag_data["exec_list"] = []
        if not exec_list:
            return
        widget = root.winfo_containing(root.winfo_pointerx(), root.winfo_pointery())
        if widget in cell_by_widget:
            assign_execs_to_cells(cell_by_widget[widget], exec_list)

    def _grid_ordered_cells():
        return sorted(grid_cells, key=lambda c: (c["row"], c["col"]))

    def assign_execs_to_cells(start_cell, exec_list):
        ordered = _grid_ordered_cells()
        if start_cell not in ordered:
            return
        start_index = ordered.index(start_cell)
        exec_index = 0
        for cell in ordered[start_index:]:
            if exec_index >= len(exec_list):
                break
            if cell.get("nav"):
                continue
            assign_exec_to_cell(cell, exec_list[exec_index])
            exec_index += 1

    def _parse_exec_and_mode(control):
        if not isinstance(control, dict):
            return None, None
        steps = control.get("steps", {})
        step0 = steps.get("0", {})
        action_sets = step0.get("action_sets", {})
        down = action_sets.get("down", [])
        up = action_sets.get("up", [])
        exec_id = _extract_exec_id_from_control(control)
        mode = "go"
        down_cmds = " ".join(
            action.get("options", {}).get("command", "").upper() for action in down
        )
        up_cmds = " ".join(
            action.get("options", {}).get("command", "").upper() for action in up
        )
        if "GO EXECUTOR" in down_cmds and "OFF EXECUTOR" in up_cmds:
            mode = "temp"
        elif "TOGGLE EXECUTOR" in down_cmds:
            mode = "go"
        return exec_id, mode

    def _load_page_object(page_obj):
        loading_page["value"] = True
        nav_dirty["value"] = False
        page_name_var.set(page_obj.get("name") or "import")
        controls = page_obj.get("controls", {})
        nav_present = False
        try:
            nav_present = (
                controls.get("0", {}).get("0", {}).get("type") == "pageup"
                and controls.get("1", {}).get("0", {}).get("type") == "pagenum"
                and controls.get("2", {}).get("0", {}).get("type") == "pagedown"
            )
        except AttributeError:
            nav_present = False
        keep_nav.set(nav_present)
        init_grid()
        for cell in grid_cells:
            row_key = str(cell["row"])
            col_key = str(cell["col"])
            control = controls.get(row_key, {}).get(col_key)
            if not isinstance(control, dict) or control.get("type") != "button":
                continue
            exec_id, mode = _parse_exec_and_mode(control)
            label = control.get("style", {}).get("text") or ""
            bgcolor = control.get("style", {}).get("bgcolor")
            cell["exec_id"] = exec_id
            cell["label"] = label
            cell["mode"] = mode or "go"
            cell["bgcolor"] = bgcolor
            cell["bg_hex"] = color_hex_by_value.get(bgcolor)
            cell["dirty"] = False
            cell["original_sig"] = _cell_signature(cell)
            update_cell_button(cell)
        loading_page["value"] = False

    def _load_page(page_key):
        page_obj = pages_container.get(page_key, {})
        _load_page_object(page_obj)

    def _reset_new_page():
        loading_page["value"] = True
        nav_dirty["value"] = False
        page_name_var.set("import")
        keep_nav.set(not args.no_nav)
        init_grid()
        for cell in grid_cells:
            cell["dirty"] = False
            cell["original_sig"] = None
        loading_page["value"] = False

    def _on_page_select(_event=None):
        value = page_select_var.get()
        if value == "new":
            current_page_key["value"] = None
            _reset_new_page()
            return
        key = value.split(":", 1)[0].strip()
        current_page_key["value"] = key
        _load_page(key)

    page_select.bind("<<ComboboxSelected>>", _on_page_select)
    _reset_new_page()

    exec_tree.bind("<ButtonPress-1>", lambda e: on_tree_press(exec_tree, e))
    exec_tree.bind("<B1-Motion>", on_tree_motion)
    exec_tree.bind("<ButtonRelease-1>", on_tree_release)
    xkey_tree.bind("<ButtonPress-1>", lambda e: on_tree_press(xkey_tree, e))
    xkey_tree.bind("<B1-Motion>", on_tree_motion)
    xkey_tree.bind("<ButtonRelease-1>", on_tree_release)

    def on_keep_nav_toggle():
        if not loading_page["value"]:
            nav_dirty["value"] = True
        snapshot = {}
        for cell in grid_cells:
            if cell.get("nav"):
                continue
            if not cell.get("exec_id"):
                continue
            snapshot[(cell["row"], cell["col"])] = {
                "exec_id": cell.get("exec_id"),
                "label": cell.get("label"),
                "mode": cell.get("mode"),
                "bgcolor": cell.get("bgcolor"),
                "bg_hex": cell.get("bg_hex"),
            }
        init_grid()
        for cell in grid_cells:
            data = snapshot.get((cell["row"], cell["col"]))
            if not data or cell.get("nav"):
                continue
            cell["exec_id"] = data["exec_id"]
            cell["label"] = data["label"]
            cell["mode"] = data["mode"]
            cell["bgcolor"] = data["bgcolor"]
            cell["bg_hex"] = data["bg_hex"]
            _mark_dirty(cell)
            update_cell_button(cell)

    keep_nav.trace_add("write", lambda *_: on_keep_nav_toggle())

    def on_save():
        blink_vars = set()
        for cell in grid_cells:
            if cell.get("nav"):
                continue
            if cell.get("mode") != "go":
                continue
            exec_id = cell.get("exec_id")
            if not exec_id:
                continue
            blink_vars.add(_blink_var_for_exec(exec_id))
        for var_name in blink_vars:
            _ensure_custom_variable(config, var_name, "false")

        page_name = page_name_var.get().strip() or "import"
        if current_page_key["value"] is None:
            page_obj = {
                "id": _make_id(),
                "name": page_name,
                "controls": _build_controls_from_grid(
                    grid_cells,
                    connection_id,
                    keep_nav.get(),
                    blink_connection_id,
                ),
                "gridSize": {
                    "minColumn": 0,
                    "maxColumn": 7,
                    "minRow": 0,
                    "maxRow": 3,
                },
            }

            page_index = _next_page_index(pages_container)
            pages_container[str(page_index)] = page_obj
            message = f"Added page {page_index} named '{page_name}'"
            page_choices[:] = ["new"]
            for k in sorted(pages_container.keys(), key=lambda k: int(k)):
                page_obj = pages_container.get(k, {})
                name = page_obj.get("name") or f"Page {k}"
                page_choices.append(f"{k}: {name}")
            page_select["values"] = page_choices
            selection = f"{page_index}: {page_name}"
            page_select_var.set(selection)
            current_page_key["value"] = str(page_index)
            _load_page(str(page_index))
        else:
            key = current_page_key["value"]
            page_obj = pages_container.get(key, {})
            page_obj["name"] = page_name
            controls = page_obj.get("controls", {})
            if not isinstance(controls, dict):
                controls = {}
            if nav_dirty["value"]:
                if keep_nav.get():
                    controls.setdefault("0", {})["0"] = {"type": "pageup"}
                    controls.setdefault("1", {})["0"] = {"type": "pagenum"}
                    controls.setdefault("2", {})["0"] = {"type": "pagedown"}
                else:
                    for row_key in ("0", "1", "2"):
                        row = controls.get(row_key, {})
                        if isinstance(row, dict):
                            row.pop("0", None)
            for cell in grid_cells:
                if not cell.get("dirty"):
                    continue
                row_key = str(cell["row"])
                col_key = str(cell["col"])
                controls.setdefault(row_key, {})
                if cell.get("exec_id"):
                    controls[row_key][col_key] = _make_button(
                        cell["label"],
                        cell["exec_id"],
                        connection_id,
                        mode=cell.get("mode", "go"),
                        bgcolor=cell.get("bgcolor") or 0,
                        blink_connection_id=blink_connection_id,
                        blink_var=_blink_var_for_exec(cell["exec_id"]),
                    )
                else:
                    controls[row_key].pop(col_key, None)
            page_obj["controls"] = controls
            message = f"Updated page {key} ('{page_name}')"
        _sync_page_lists(config, pages_container)
        output_path = args.out or args.config
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        _refresh_tree_mappings()
        messagebox.showinfo("Saved", f"{message} to {output_path}.")

    def _build_page_from_grid(page_name):
        return {
            "id": _make_id(),
            "name": page_name,
            "controls": _build_controls_from_grid(
                grid_cells,
                connection_id,
                keep_nav.get(),
                blink_connection_id,
            ),
            "gridSize": {
                "minColumn": 0,
                "maxColumn": 7,
                "minRow": 0,
                "maxRow": 3,
            },
        }

    def on_export_page():
        page_name = page_name_var.get().strip() or "import"
        export_path = filedialog.asksaveasfilename(
            title="Export Page",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not export_path:
            return
        page_obj = _build_page_from_grid(page_name)
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(page_obj, f, indent=2)
            f.write("\n")
        messagebox.showinfo("Exported", f"Page exported to {export_path}.")

    def on_import_page():
        import_path = filedialog.askopenfilename(
            title="Import Page",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not import_path:
            return
        try:
            with open(import_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            messagebox.showerror("Import", f"Unable to read file: {exc}")
            return
        page_obj = None
        if isinstance(data, dict) and "controls" in data:
            page_obj = data
        elif isinstance(data, dict) and "page" in data and isinstance(data["page"], dict):
            page_obj = data["page"]
        if not page_obj:
            messagebox.showerror(
                "Import",
                "Invalid page file. Expected a page object with 'controls'.",
            )
            return
        current_page_key["value"] = None
        page_select_var.set("new")
        _load_page_object(page_obj)

    def on_delete_page():
        if current_page_key["value"] is None:
            messagebox.showwarning("Delete", "Select an existing page to delete.")
            return
        key = current_page_key["value"]
        page_obj = pages_container.get(key, {})
        name = page_obj.get("name") or key
        if not messagebox.askyesno(
            "Delete",
            f"Delete page {key} ('{name}')? This cannot be undone.",
        ):
            return
        pages_container.pop(key, None)
        mapping = _compact_pages_container(pages_container)
        _sync_page_lists(config, pages_container, mapping)
        page_choices[:] = ["new"]
        for k in sorted(pages_container.keys(), key=lambda k: int(k)):
            page_obj = pages_container.get(k, {})
            name = page_obj.get("name") or f"Page {k}"
            page_choices.append(f"{k}: {name}")
        page_select["values"] = page_choices
        page_select_var.set("new")
        current_page_key["value"] = None
        _reset_new_page()
        _refresh_tree_mappings()
        output_path = args.out or args.config
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        messagebox.showinfo("Deleted", f"Page {key} removed from {output_path}.")

    def on_close():
        root.destroy()

    action_frame = ttk.Frame(right_frame)
    action_frame.pack(fill="x", pady=(8, 0))
    ttk.Button(action_frame, text="Export Page", command=on_export_page).pack(
        side="left", padx=4
    )
    ttk.Button(action_frame, text="Import Page", command=on_import_page).pack(
        side="left", padx=4
    )
    ttk.Button(action_frame, text="Delete Page", command=on_delete_page).pack(
        side="right", padx=4
    )
    ttk.Button(action_frame, text="Save Page", command=on_save).pack(
        side="right", padx=4
    )
    ttk.Button(action_frame, text="Close", command=on_close).pack(
        side="right", padx=4
    )

    root.update_idletasks()
    req_width = root.winfo_reqwidth()
    current_height = root.winfo_height()
    if current_height <= 1:
        current_height = root.winfo_reqheight()
    root.geometry(f"{req_width}x{current_height}")

    root.mainloop()


def main():
    parser = argparse.ArgumentParser(
        description="Export GrandMA2 button executors to Bitfocus Companion page JSON."
    )
    parser.add_argument("--host", default=None, help="GrandMA2 host.")
    parser.add_argument("--port", type=int, default=None, help="GrandMA2 telnet port.")
    parser.add_argument("--user", default=None, help="GrandMA2 user.")
    parser.add_argument("--password", default=None, help="GrandMA2 password.")
    parser.add_argument(
        "--blink-connection-id",
        default=None,
        help="Companion connectionId for the Generic Blink feedback (optional).",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the Companion config JSON to update.",
    )
    parser.add_argument(
        "--no-nav",
        action="store_true",
        help="Do not add the default page up/down buttons.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write updated config to this file (default: overwrite input).",
    )
    args = parser.parse_args()

    try:
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"Config file not found: {args.config}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Config file is not valid JSON: {exc}")

    connection_id, conn = _find_grandma_connection(config)
    blink_connection_id = args.blink_connection_id or _find_blink_connection_id(config)
    if not blink_connection_id:
        raise SystemExit(
            "Could not find a Generic Blink connection for feedback. "
            "Pass --blink-connection-id or add a blinkVariable feedback in the config."
        )
    config_host, config_port = _extract_host_port(conn)
    config_user, config_password = _extract_login(conn)
    host = args.host or config_host or "127.0.0.1"
    port = args.port or config_port or 30000
    user = args.user or config_user
    password = args.password or config_password
    if not user or not password:
        raise SystemExit(
            "Missing GrandMA2 login/password. Provide --user/--password "
            "or ensure they exist in the Companion config."
        )

    _run_gui(
        args,
        config,
        connection_id,
        blink_connection_id,
        host,
        port,
        user,
        password,
    )


if __name__ == "__main__":
    main()
