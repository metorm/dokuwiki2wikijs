#! /usr/bin/env python3

import os
import sys
from zipfile import ZipFile
from os.path import basename
from shutil import rmtree, copyfile
import subprocess
import re


tmp_prefix = os.path.join("/tmp", "dokuwiki2wikijs")


def pandoc(infile):
    result = subprocess.run(
        ["pandoc", "-f", "dokuwiki", "-t", "markdown_mmd", "--wrap=none", infile],
        stdout=subprocess.PIPE,
    )
    if len(result.stdout) == 0:
        raise ValueError(
            "Pandoc returned no output for file `%s`. This usually means that Pandoc encountered a syntax error in the input file and crashed. Try running `pandoc -f dokuwiki -t markdown_mmd --wrap=none AFFECTED_FILE` on the affected file to see if there is a syntax error."
            % infile
        )
    return result.stdout.decode("utf-8")


def first_heading_or_filename(lines, pagename):
    if lines[0][0] == "#":
        title = lines[0].partition(" ")[2].strip()
    else:
        title = pagename
    return title


def get_metadata(lines, pagename):
    # We surround the title with quotes to ensure Wiki.js can import it.
    # Wiki.js requires a string and some possible titles are not considered
    # strings (e.g. dates)
    return {"title": '"' + first_heading_or_filename(lines, pagename) + '"'}


def starts_with_text(line):
    # Empty line -> no
    if len(line.strip()) == 0:
        return False
    # Letters and quote -> yes
    if line[0].isalpha() or line[0] == '"':
        return True
    # Numeric list marker -> no
    if re.match(r"^[0-9]+\. ", line):
        return False
    return False


def unwrap_sentences(lines):
    # pandoc wraps markdown paragraphs however the input was formatted,
    # so unwrap it according to markdown/asciidoc conventions (one sentence
    # per line).
    result = []
    compacted_lines = []
    doing_compacting = False
    compacted_line = ""

    for line in lines:
        if not doing_compacting:
            if len(line) > 0 and line[-1] != ".":
                doing_compacting = True
                compacted_line = line
            else:
                compacted_lines.append(line)
        else:
            if starts_with_text(line):
                compacted_line = compacted_line + " " + line
            else:
                compacted_lines.append(compacted_line)
                compacted_lines.append(line)
                compacted_line = ""
                doing_compacting = False
    if compacted_line != "":
        compacted_lines.append(compacted_line)
    for line in compacted_lines:
        while ". " in line:
            line1, line = line.split(". ", 1)
            result.append(line1 + ".")
        result.append(line)
    return result


def find_next_link_start(line, pos):
    # 使用精确位置检测数学表达式边界
    math_ranges = [m.span() for m in re.finditer(r"\$\$.*?\$\$", line)]
    for match in re.finditer(r"(\[\[|\{\{)", line[pos:]):
        link_start = pos + match.start()
        # 检查是否在数学表达式范围内
        in_math = any(start <= link_start < end for (start, end) in math_ranges)
        if not in_math:
            return link_start
    return -1


def convert_links(lines):
    # print(f"Converting {len(lines)} links...")
    for i, line in enumerate(lines):
        pos = find_next_link_start(line, 0)
        dead_loop_counter = 0
        while pos != -1:
            pattern = r"(\[\[|\{\{)(?P<uri>[^\|]+?)(\|(?P<text>.+?)?)?(\]\]|\}\})"
            # print(f"re search in  {line[pos:]}: pos = {pos}")
            match = re.search(pattern, line[pos:])
            if match:
                text = match.group("text")
                uri = match.group("uri").rstrip("|")
                if not uri.startswith("http"):
                    uri = uri.replace(":", "/")
                if not text:
                    text = uri
                # Ensure internal uri starts at the root
                if not uri.startswith("http") and not uri.startswith("/"):
                    uri = "/" + uri
                link = "[%s](%s)" % (text, uri)
                line = re.sub(pattern, link, line, count=1)
            prvpos = pos
            pos = find_next_link_start(line, pos)
            if prvpos >= pos:
                dead_loop_counter += 1

            if dead_loop_counter > 2:
                print(
                    f"\nError: we may have falled in dead loop at line {i}: {lines[i]}"
                )
                print("Beak from this line, you may need to review the result.")
                break
        lines[i] = line
    return lines


def wrap_kind(tag):
    words = tag.split(" ")
    if "info" in words or "notice" in words:
        return "{.is-info}"
    if "important" in words or "warning" in words or "caution" in words:
        return "{.is-warning}"
    if "alert" in words or "danger" in words:
        return "{.is-danger}"
    if "tip" in words or "help" in words or "todo" in words:
        return "{.is-danger}"
    if "safety" in words or "danger" in words:
        return "{.is-danger}"
    return ""


def convert_wrap(lines):
    kind = "{.is-info}"
    wrapping = False
    for i, line in enumerate(lines):
        # This might be a pandoc'ed markdown in which case tags are escaped
        if line.startswith("<WRAP") or line.startswith(r"\<WRAP"):
            tag, line = line.split(">", 1)
            kind = wrap_kind(tag)
            lines[i] = "> " + line
            wrapping = True
        if "</WRAP>" in line:
            lines[i] = lines[i].replace("</WRAP>", kind)
            wrapping = False
        if r"\</WRAP\>" in line:
            lines[i] = lines[i].replace(r"\</WRAP\>", kind)
            wrapping = False
        if wrapping:
            lines[i] = "> " + line
    return lines


def add_metadata(lines, metadata):
    lines.insert(0, "---")
    for key, value in metadata.items():
        lines.insert(1, key + ": " + value)
    lines.insert(len(metadata) + 1, "---")


def convert_filename_to_unicode(line):
    # Only handles the ones we needed...
    line = line.replace("%C3%84", "Ä")
    line = line.replace("%C3%85", "Å")
    line = line.replace("%C3%89", "É")
    line = line.replace("%C3%96", "Ö")
    line = line.replace("%C3%A4", "ä")
    line = line.replace("%C3%A5", "å")
    line = line.replace("%C3%A9", "é")
    line = line.replace("%C3%B6", "ö")
    return line


def ensure_path_exists(path):
    directory = os.path.dirname(path)
    if not os.path.exists(directory):
        os.makedirs(directory)


def is_markdown(filename):
    with open(filename, "r", encoding="utf-8") as f:
        first_line = f.readline()
    return first_line[0] == "#"


def read_users(path):
    users_file = os.path.join(path, "conf", "users.auth.php")
    with open(users_file, "r") as f:
        for line in f:
            if not line.startswith("#") and len(line) > 1:
                userparts = line.split(":")
                assert len(userparts) == 5
                users[userparts[0]] = {"name": userparts[2], "email": userparts[3]}


def remove_useless_tags(lines):
    new_lines = []
    for line in lines:
        line = line.replace("\\<sortable\\>", "")
        line = line.replace("\\</sortable\\>", "")
        new_lines.append(line)
    return new_lines


def convert_file(txtfile, title):
    if is_markdown(txtfile):
        with open(txtfile) as file:
            lines = file.read().splitlines()
        lines = convert_links(lines)
    else:
        lines = str(pandoc(txtfile)).split("\n")
    lines = remove_useless_tags(lines)
    lines = convert_wrap(lines)
    # lines = unwrap_sentences(lines)
    metadata = get_metadata(lines, title)
    add_metadata(lines, metadata)

    return lines


def temporary_file_for(pathname):
    parts = pathname.split(os.sep)
    temporary = os.path.join(tmp_prefix, *parts[parts.index("data") :])
    return temporary


def collect_and_convert_all_pages():
    for folder, _, files in os.walk(os.path.join(path, "data", "pages")):
        txt_files = (file for file in files if file.endswith(".txt"))
        for f in txt_files:
            filename_with_txt = os.path.join(folder, f)
            filename = temporary_file_for(
                convert_filename_to_unicode(filename_with_txt[:-4])
            )
            basename = os.path.basename(filename)
            ensure_path_exists(filename)

            if basename == "sidebar":
                continue

            print(filename_with_txt + "(" + basename + ")... ", end="", flush=True)

            lines = convert_file(filename_with_txt, basename)

            filename_with_md = os.path.join(tmp_prefix, filename + ".md")
            if basename == "start":
                filename_with_md = filename_with_md.replace("start.md", "home.md")

            with open(filename_with_md, "w", encoding="utf-8") as file:
                file.writelines("\n".join(lines))

            print(len(lines), "lines")


def collect_all_media():
    for folder, _, media_files in os.walk(os.path.join(path, "data", "media")):
        for f in media_files:
            media_file = os.path.join(folder, f)
            print(media_file, "...", end="", flush=True)
            filename = temporary_file_for(convert_filename_to_unicode(media_file))
            filename = filename.replace("media", "pages")
            ensure_path_exists(filename)
            copyfile(media_file, filename)
            print(" copied")


if __name__ == "__main__":

    if len(sys.argv) != 2:
        print("Usage: %s <file or folder>" % sys.argv[0])
        sys.exit(1)

    path = sys.argv[1]
    if not os.path.exists(path):
        print("'%s' doesn't exist" % path)
        sys.exit(1)

    if os.path.isfile(path):
        lines = convert_file(path, path)
        print("\n".join(lines))
    else:
        if not os.path.exists(os.path.join(path, "data", "pages")):
            print(
                "The folder given as argument should be at the root of a dokuwiki installation or copy"
            )
            sys.exit(-1)

        users = {}
        read_users(path)

        rmtree(tmp_prefix, ignore_errors=True)
        os.makedirs(tmp_prefix)

        collect_and_convert_all_pages()
        collect_all_media()

        print("Compressing to 'dokuwiki2wikijs.zip'... ", end="", flush=True)
        with ZipFile("dokuwiki2wikijs.zip", "w") as zipObj:
            # Walk through the files in the data/pages subdir
            curdir = os.getcwd()
            os.chdir(os.path.join(tmp_prefix, "data", "pages"))
            for folder, folders, files in os.walk("."):
                for file in files:
                    zipObj.write(os.path.join(folder, file))
        print("done")
