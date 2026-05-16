"""
AUTO-PARALLELIZER + BENCHMARK — CSCI465/ECEN433, Nile University, Spring 2026

Usage:
  python3 auto_parallelizer.py <input.c> <output.c>
  python3 auto_parallelizer.py <input.c> <output.c> --speedup
  python3 auto_parallelizer.py <input.c> <output.c> --benchmark

Flags:
  --speedup    parallelizes then measures speedup + accuracy (default if no flag)
  --benchmark  runs over matrix sizes 128/256/512 x thread counts 1/2/4/8

The C file must print:  printf("Time: %.4f seconds\\n", elapsed);
"""

import re, sys, os, subprocess, json

# ==============================================================================
# STEP 3: DEPENDENCY ANALYSIS
# ==============================================================================

def _arrays_written_in_loop(loop_var, loop_body):
    v = re.escape(loop_var)
    written = set(re.findall(
        r'(\w+)\s*\[[\w\s\+\-\*]+\]\s*(?:\[[\w\s\+\-\*]+\])?\s*[\+\-\*]?=',
        loop_body
    ))
    written |= set(re.findall(
        r'(\w+)\s*\[[^\]]*\b' + v + r'\b[^\]]*\]\s*(?:[\+\-\*]=|=)',
        loop_body
    ))
    return written


def _loop_carried_array_access(loop_var, loop_body):
    written = _arrays_written_in_loop(loop_var, loop_body)
    if not written:
        return False, None
    v = re.escape(loop_var)
    for pat in [
        r'(\w+)\s*\[[^\]]*\b' + v + r'\b\s*[\+\-]\s*\d+[^\]]*\]',
        r'(\w+)\s*\[[^\]]*\d+\s*[\+\-]\s*\b' + v + r'\b[^\]]*\]',
    ]:
        for arr in re.findall(pat, loop_body):
            if arr in written:
                return True, (
                    f"loop-carried dependency: '{arr}' written with [{loop_var}] "
                    f"and read at [{loop_var}±offset]"
                )
    return False, None


def _get_sequential_forloop_sections(loop_body):
    lines = loop_body.split('\n')
    sections = []
    depth = 0
    start_idx = None
    for idx, line in enumerate(lines):
        depth += line.count('{') - line.count('}')
        if depth >= 1:
            start_idx = idx + 1
            break
    if start_idx is None:
        return sections
    depth = 1
    i = start_idx
    while i < len(lines):
        line = lines[i]
        if depth == 1 and FOR_RE.match(line):
            sec = get_loop_body_str(lines, i)
            sections.append(sec)
            i += sec.count('\n') + 1
            continue
        depth += line.count('{') - line.count('}')
        if depth <= 0:
            break
        i += 1
    return sections


def _multi_subloop_dependency(loop_var, loop_body):
    sections = _get_sequential_forloop_sections(loop_body)
    if len(sections) < 2:
        return False, None
    v = re.escape(loop_var)
    acc = []
    for sec in sections:
        written = set(re.findall(
            r'\b(\w+)\s*\[[^\]]*\]\s*(?:\[[^\]]*\])?\s*(?:\[[^\]]*\])?\s*[\+\-\*]?=(?!=)',
            sec
        ))
        all_arr = set(re.findall(r'\b(\w+)\s*\[', sec))
        acc.append((written, all_arr - written))
    for i in range(len(acc)):
        for j in range(i + 1, len(acc)):
            cross = (acc[i][0] & acc[j][1]) | (acc[i][1] & acc[j][0])
            for arr in cross:
                subs = re.findall(
                    r'\b' + re.escape(arr) + r'\s*(\[[^\]]*\](?:\[[^\]]*\])*)',
                    loop_body
                )
                if not any(re.search(r'\b' + v + r'\b', s) for s in subs):
                    return True, (
                        f"stencil time-step: '{arr}' written in sub-loop {i+1} "
                        f"and read in sub-loop {j+1}"
                    )
    return False, None


def _find_inner_sequential_loops(lines, outer_loop_line):
    body = get_loop_body_str(lines, outer_loop_line)
    body_lines = body.split('\n')
    depth = 0
    body_start = None
    for idx, line in enumerate(body_lines):
        depth += line.count('{') - line.count('}')
        if depth >= 1:
            body_start = idx + 1
            break
    if body_start is None:
        return []
    results = []
    depth = 1
    i = body_start
    while i < len(body_lines):
        line = body_lines[i]
        if depth == 1 and FOR_RE.match(line):
            m = FOR_RE.match(line)
            results.append((outer_loop_line + i, m.group(2), m.group(1)))
            sec = get_loop_body_str(body_lines, i)
            i += sec.count('\n') + 1
            continue
        depth += line.count('{') - line.count('}')
        if depth <= 0:
            break
        i += 1
    return results


def dependency_analysis(loop_var, loop_body):
    nl = re.search(r'\[\s*\w+\s*\*\s*\w+\s*\]', loop_body)
    if nl:
        return False, f"non-linear subscript {nl.group()}"

    carried, msg = _loop_carried_array_access(loop_var, loop_body)
    if carried:
        return False, msg

    seen = {}
    for arr, sub in re.findall(
        r'(\w+)\s*(\[[\w\s\+\-\*]+\](?:\[[\w\s\+\-\*]+\])?)\s*[\+\-\*]?=',
        loop_body
    ):
        if arr in seen and seen[arr] != sub:
            return False, f"WAW: '{arr}' written at two different subscripts"
        seen[arr] = sub

    multi, msg = _multi_subloop_dependency(loop_var, loop_body)
    if multi:
        return False, msg

    return True, "Safe"


def nest_dependency_check(outer_loop_var, body_text):
    lines = body_text.split('\n')
    i = 0
    while i < len(lines):
        m = FOR_RE.match(lines[i])
        if m:
            loop_var  = m.group(2)
            loop_body = get_loop_body_str(lines, i)
            is_safe, reason = dependency_analysis(loop_var, loop_body)
            if not is_safe:
                prefix = "" if loop_var == outer_loop_var else f"nested loop '{loop_var}': "
                return False, prefix + reason
            i += max(1, loop_body.count('\n'))
        else:
            i += 1
    return True, "Safe"


# ==============================================================================
# STEP 4: VARIABLE CLASSIFICATION (AutOMP)
# ==============================================================================

def classify_variables(all_loop_vars, loop_body):
    """
    PRIVATE   = loop counters + scalars assigned inside
    SHARED    = arrays (each thread touches a different element)
    REDUCTION = scalars using += -= *= across iterations (REQUIRED for correctness)
    """
    private_vars   = set(all_loop_vars)
    shared_vars    = set()
    reduction_vars = []
    local_accums   = []

    skip = {'for','if','while','int','double','float',
            'printf','scanf','return','void','rand'}

    for m in re.finditer(r'(\w+)\s*\[', loop_body):
        name = m.group(1)
        if name not in all_loop_vars and name not in skip:
            shared_vars.add(name)

    local_vars = set(re.findall(
        r'(?:int|long|double|float|short|unsigned)\s+(\w+)\s*=',
        loop_body
    ))

    def _add_reduction(var, op):
        if var in all_loop_vars:
            return
        if var in local_vars:
            if var not in local_accums:
                local_accums.append(var)
            return
        pos  = loop_body.index(var)
        rest = loop_body[pos + len(var):pos + len(var) + 2].strip()
        if not rest.startswith('[') and var not in [r[0] for r in reduction_vars]:
            reduction_vars.append((var, op))
            shared_vars.discard(var)
            private_vars.discard(var)

    for m in re.finditer(r'\b(\w+)\s*(\+=|-=|\*=)', loop_body):
        _add_reduction(m.group(1), m.group(2)[0])
    for m in re.finditer(r'\b(\w+)\s*=\s*\1\s*[\+\-\*]', loop_body):
        _add_reduction(m.group(1), '+')

    for lv in local_vars:
        shared_vars.discard(lv)
        private_vars.discard(lv)

    return sorted(private_vars), sorted(shared_vars), reduction_vars, sorted(local_accums)


# ==============================================================================
# STEP 5: SCHEDULE DETECTION + PRAGMA BUILD
# ==============================================================================

def detect_schedule(loop_body):
    loop_var_names = set(re.findall(r'for\s*\(\s*(?:\w+\s+){0,2}(\w+)\s*=', loop_body))
    for condition in re.findall(r'for\s*\([^;]+;([^;]+);[^)]+\)', loop_body):
        m = re.search(r'\b(\w+)\b\s*[<>]=?\s*\b(\w+)\b', condition)
        if m:
            right = m.group(2)
            if right in loop_var_names and right.islower():
                return "dynamic"
    if re.search(r'\bif\s*\(', loop_body):
        return "dynamic"
    return "static"


def build_pragma(private_vars, shared_vars, reduction_vars, schedule="static"):
    pragma = f"#pragma omp parallel for schedule({schedule})"
    if private_vars:
        pragma += f" private({','.join(private_vars)})"
    if shared_vars:
        pragma += f" shared({','.join(shared_vars)})"
    for var, op in reduction_vars:
        pragma += f" reduction({op}:{var})"
    return pragma


# ==============================================================================
# HELPERS: LOOP EXTRACTION
# ==============================================================================

FOR_RE = re.compile(
    r'^(\s*)for\s*\('
    r'\s*(?:\w+\s+){0,2}'
    r'(\w+)\s*=\s*[^;]+;'
    r'\s*[^;]+;'
    r'\s*[^)]+\)'
)


def get_loop_body_str(lines, start):
    body = []
    depth = 0
    found_brace = False
    i = start
    base_indent = len(lines[start]) - len(lines[start].lstrip())
    while i < len(lines):
        line = lines[i]
        body.append(line)
        depth += line.count('{') - line.count('}')
        if '{' in line:
            found_brace = True
        if found_brace and depth == 0:
            break
        if not found_brace and i > start:
            stripped = line.strip()
            if stripped == '':
                break
            cur_indent = len(line) - len(line.lstrip())
            if cur_indent <= base_indent and not stripped.startswith('for'):
                break
        i += 1
    return '\n'.join(body)


def get_all_for_vars(text):
    all_vars = []
    i = 0
    while i < len(text):
        m = re.search(r'for\s*\(', text[i:])
        if not m:
            break
        abs_start = i + m.end()
        depth, pos = 1, abs_start
        while pos < len(text) and depth > 0:
            if text[pos] == '(':   depth += 1
            elif text[pos] == ')': depth -= 1
            pos += 1
        header = text[abs_start:pos-1]
        i = abs_start
        parts, current, d = [], '', 0
        for ch in header:
            if ch == '(':   d += 1; current += ch
            elif ch == ')': d -= 1; current += ch
            elif ch == ';' and d == 0: parts.append(current.strip()); current = ''
            else: current += ch
        parts.append(current.strip())
        if len(parts) < 3:
            continue
        init_m = re.search(r'(?:\w+\s+){0,2}(\w+)\s*=', parts[0])
        if init_m:
            all_vars.append(init_m.group(1))
        incr_m = re.search(r'(?:\+\+|--)?(\w+)(?:\+\+|--|\s*[\+\-\*]=)?', parts[2].strip())
        if incr_m:
            all_vars.append(incr_m.group(1))
    skip = {'0','1','2','3','4','if','for','while','int','long','double','float'}
    seen = []
    for v in all_vars:
        if v not in seen and v not in skip and not v.isdigit():
            seen.append(v)
    return seen


def find_outermost_loops(lines):
    functions, current_func = [], []
    brace_depth, in_func = 0, False
    for i, line in enumerate(lines):
        opens, closes = line.count('{'), line.count('}')
        if not in_func and opens > 0 and brace_depth == 0:
            in_func = True
            current_func = []
        if in_func:
            current_func.append((i, line))
        brace_depth += opens - closes
        if in_func and brace_depth == 0:
            functions.append(current_func)
            current_func = []
            in_func = False
    results = []
    for func_lines in functions:
        for_loops = [
            (i, m.group(2), m.group(1), len(m.group(1)))
            for i, line in func_lines
            for m in [FOR_RE.match(line)] if m
        ]
        if not for_loops:
            continue
        min_indent = min(fl[3] for fl in for_loops)
        for i, loop_var, indent, indent_len in for_loops:
            if indent_len == min_indent:
                results.append((i, loop_var, indent))
    return results


# ==============================================================================
# MAIN: AUTO-PARALLELIZE
# ==============================================================================

def auto_parallelize(input_file, output_file):
    print("=" * 65)
    print("  AUTO-PARALLELIZER — CSCI465/ECEN433, Nile University")
    print(f"  Input : {input_file}  →  Output: {output_file}")
    print("=" * 65)

    with open(input_file) as f:
        lines = f.read().split('\n')

    has_omp_h = any('#include <omp.h>' in line for line in lines)
    outer_loops = find_outermost_loops(lines)
    insertions  = {}

    for loop_line, loop_var, indent in outer_loops:
        body_text = get_loop_body_str(lines, loop_line)
        all_lv    = get_all_for_vars(body_text)
        if loop_var not in all_lv:
            all_lv = [loop_var] + all_lv

        is_safe, reason = nest_dependency_check(loop_var, body_text)

        if not is_safe:
            insertions[loop_line] = indent + f"/* OMP blocked: {reason} */"
            print(f"  Line {loop_line+1:>4}: for({loop_var}=...)  ✗ BLOCKED — {reason}")

            if "stencil" in reason or "sub-loop" in reason:
                all_body_lv = get_all_for_vars(body_text)
                if loop_var not in all_body_lv:
                    all_body_lv = [loop_var] + all_body_lv
                _, shared_body, _, _ = classify_variables(all_body_lv, body_text)
                par_pragma = f"#pragma omp parallel private({','.join(all_body_lv)})"
                if shared_body:
                    par_pragma += f" shared({','.join(sorted(shared_body))})"
                insertions[loop_line] = (indent + par_pragma + "\n" +
                                         indent + f"/* OMP blocked: {reason} */")

                for inner_line, inner_var, inner_indent in \
                        _find_inner_sequential_loops(lines, loop_line):
                    inner_body = get_loop_body_str(lines, inner_line)
                    inner_lv   = get_all_for_vars(inner_body)
                    if inner_var not in inner_lv:
                        inner_lv = [inner_var] + inner_lv
                    inner_safe, inner_reason = nest_dependency_check(inner_var, inner_body)
                    if inner_safe:
                        _, _, rv, _ = classify_variables(inner_lv, inner_body)
                        sched = detect_schedule(inner_body)
                        pragma = f"#pragma omp for schedule({sched})"
                        for v, op in rv:
                            pragma += f" reduction({op}:{v})"
                        print(f"       ↳ Line {inner_line+1:>4}: for({inner_var}=...)  ✓ INNER — {pragma}")
                        insertions[inner_line] = inner_indent + pragma
                    else:
                        print(f"       ↳ Line {inner_line+1:>4}: for({inner_var}=...)  ✗ inner blocked — {inner_reason}")
            continue

        private_v, shared_v, reduction_v, local_acc = classify_variables(all_lv, body_text)
        schedule = detect_schedule(body_text)
        pragma   = build_pragma(private_v, shared_v, reduction_v, schedule=schedule)

        extras = []
        if reduction_v:
            extras.append("reduction: " + ", ".join(f"{op}:{v}" for v, op in reduction_v))
        if local_acc:
            extras.append(f"loop-local (auto-private): {', '.join(local_acc)}")
        extra_str = "  [" + " | ".join(extras) + "]" if extras else ""

        print(f"  Line {loop_line+1:>4}: for({loop_var}=...)  ✓ {pragma}{extra_str}")
        insertions[loop_line] = indent + pragma

    # Write output file
    out_lines = []
    omp_added = False
    for i, line in enumerate(lines):
        if not omp_added and line.strip().startswith('#include'):
            out_lines.append(line)
            if not has_omp_h:
                out_lines.append('#include <omp.h>')
            omp_added = True
            continue
        if i in insertions:
            out_lines.append(insertions[i])
        out_lines.append(line)

    header = (
        "/*\n"
        " * AUTO-PARALLELIZED — generated by auto_parallelizer.py\n"
        " * CSCI465/ECEN433 — Nile University — Spring 2026\n"
        " * Compile: gcc -O2 -fopenmp " + os.path.basename(output_file) + " -o out_par\n"
        " * Run:     OMP_NUM_THREADS=4 ./out_par\n"
        " */\n\n"
    )
    with open(output_file, 'w') as f:
        f.write(header + '\n'.join(out_lines))

    n_blocked  = sum(1 for v in insertions.values() if 'OMP blocked' in v)
    n_parallel = len(insertions) - n_blocked
    print("─" * 65)
    print(f"  Loops: {len(outer_loops)} found  |  {n_parallel} parallelized  |  {n_blocked} blocked")
    print(f"  Output: {output_file}")
    print("=" * 65)


# ==============================================================================
# COMPILE + RUN HELPERS
# ==============================================================================

def compile_c(src, binary):
    """Compile with -O2 -fopenmp. No -DN injection — file defines its own N."""
    r = subprocess.run(
        ["gcc", "-O2", "-fopenmp", src, "-o", binary, "-lm"],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        print(f"  Compile error: {r.stderr[:300]}")
        return False
    return True


def run_binary(binary, threads=1, runs=3, timeout=120):
    env = {**os.environ, "OMP_NUM_THREADS": str(threads)}
    times, vals = [], []
    for _ in range(runs):
        try:
            r = subprocess.run([binary], capture_output=True, text=True,
                               env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, None
        if r.returncode != 0:
            return None, None
        m_t = re.search(r"Time:\s*([\d.]+)", r.stdout)
        m_v = re.search(
            r'(?:Sample|Result|Value|Output|Answer|Sum|Checksum)\s*[=:]\s*([\d.\-e]+)',
            r.stdout, re.IGNORECASE
        )
        if not m_v:
            for line in r.stdout.splitlines():
                if "Time" not in line:
                    nm = re.search(r'([\d.\-e]+)\s*$', line)
                    if nm:
                        m_v = nm
        if m_t: times.append(float(m_t.group(1)))
        if m_v: vals.append(float(m_v.group(1)))
    avg_time = round(sum(times) / len(times), 4) if times else None
    avg_val  = round(sum(vals)  / len(vals),  4) if vals  else None
    return avg_time, avg_val


def check_accuracy(seq_val, par_val, tol=1e-2):
    if seq_val is None or par_val is None:
        return None, "N/A"
    diff  = abs(seq_val - par_val)
    rel   = diff / (abs(seq_val) + 1e-12)
    match = rel < tol
    label = "✓ MATCH" if match else f"✗ MISMATCH (diff={diff:.2e})"
    return match, label


def cleanup(*paths):
    for p in paths:
        if os.path.exists(p):
            os.remove(p)


# ==============================================================================
# SPEEDUP MEASUREMENT — per size (128 / 256 / 512) × thread counts
# ==============================================================================

MEASURE_SIZES = [128, 256, 512]


def _write_with_size(src, dst, size):
    """Copy src → dst, replacing #define N <val> with #define N <size>."""
    with open(src, 'r') as f:
        content = f.read()
    content = re.sub(r'#define\s+N\s+\S+', f'#define N {size}', content)
    with open(dst, 'w') as f:
        f.write(content)


def _detect_sizes(input_file):
    """Pick sizes suited to the file's original N.
    Small N (≤512): use [128,256,512]. Large N: use [N//8, N//4, N]."""
    with open(input_file, 'r') as f:
        content = f.read()
    m = re.search(r'#define\s+N\s+(\d+)', content)
    if not m:
        return MEASURE_SIZES
    orig = int(m.group(1))
    if orig <= 512:
        return MEASURE_SIZES
    sizes = sorted({max(128, orig // 8), max(256, orig // 4), orig})
    return sizes


def measure_speedup(input_file, output_file, thread_counts=[1, 2, 4, 8], runs=3):
    dir_path = os.path.dirname(os.path.abspath(input_file))

    sizes = _detect_sizes(input_file)

    print()
    print("=" * 65)
    print("  SPEEDUP + ACCURACY MEASUREMENT")
    print(f"  Sizes: {sizes}  |  Threads: {thread_counts}")
    print("=" * 65)

    all_results = {}

    for size in sizes:
        seq_src = os.path.join(dir_path, f"_bench_seq_{size}.c")
        par_src = os.path.join(dir_path, f"_bench_par_{size}.c")
        seq_bin = os.path.join(dir_path, f"_bench_seq_{size}")
        par_bin = os.path.join(dir_path, f"_bench_par_{size}")

        _write_with_size(input_file,  seq_src, size)
        _write_with_size(output_file, par_src, size)

        ok_seq = compile_c(seq_src, seq_bin)
        ok_par = compile_c(par_src, par_bin)
        cleanup(seq_src, par_src)

        if not ok_seq or not ok_par:
            print(f"\n  N={size}: compile error — skipping")
            cleanup(seq_bin, par_bin)
            continue

        seq_time, seq_val = run_binary(seq_bin, threads=1, runs=runs)
        cleanup(seq_bin)

        if seq_time is None:
            print(f"\n  N={size}: sequential run failed — skipping")
            cleanup(par_bin)
            continue

        seq_out = f"{seq_val:.6f}" if seq_val is not None else "N/A"
        print(f"\n  N={size}  seq={seq_time:.4f}s  output={seq_out}")
        print(f"  {'Threads':>7}  {'Par(s)':>8}  {'Speedup':>8}  {'Eff%':>6}  {'Par output':>12}  Accuracy")
        print("  " + "-" * 63)

        results = []
        for t in thread_counts:
            par_time, par_val = run_binary(par_bin, threads=t, runs=runs)
            if par_time is None:
                print(f"  {t:>7}  TIMEOUT/ERROR")
                continue
            safe_par = max(par_time, 1e-9)
            speedup    = round(seq_time / safe_par, 3)
            efficiency = round((speedup / t) * 100, 1)
            match, acc = check_accuracy(seq_val, par_val)
            par_out    = f"{par_val:.4f}" if par_val is not None else "N/A"
            print(f"  {t:>7}  {par_time:>8.4f}s  {speedup:>8.2f}x  {efficiency:>5.1f}%  {par_out:>12}  {acc}")
            results.append({
                "size": size, "threads": t,
                "seq_time": seq_time, "par_time": par_time,
                "speedup": speedup, "efficiency": efficiency,
                "seq_val": seq_val, "par_val": par_val, "accuracy_match": match
            })

        cleanup(par_bin)
        all_results[size] = results

    print("\n" + "=" * 65)

    flat = [r for rs in all_results.values() for r in rs]
    if flat:
        best = max(flat, key=lambda r: r['speedup'])
        out  = input_file.replace('.c', '_speedup.json')
        with open(out, 'w') as f:
            json.dump(flat, f, indent=2)
        print(f"  Best: {best['speedup']:.2f}x  (N={best['size']}, {best['threads']} threads)"
              f"  |  saved: {os.path.basename(out)}")
    print("=" * 65)
    return all_results


# ==============================================================================
# BENCHMARK (128 / 256 / 512 using fresh templates — no -DN injection)
# ==============================================================================

SEQ_TEMPLATE = '''#include <stdio.h>
#include <omp.h>
#define N {N}
double A[N][N], B[N][N], C[N][N];
void initialize() {{
    int i, j;
    for (i = 0; i < N; i++)
        for (j = 0; j < N; j++) {{
            A[i][j] = (double)(i + j + 1);
            B[i][j] = (double)(i * j + 1);
            C[i][j] = 0.0;
        }}
}}
void matmul() {{
    int i, j, k;
    for (i = 0; i < N; i++)
        for (j = 0; j < N; j++)
            for (k = 0; k < N; k++)
                C[i][j] += A[i][k] * B[k][j];
}}
int main() {{
    initialize();
    double start = omp_get_wtime();
    matmul();
    printf("Time: %.4f seconds\\n", omp_get_wtime() - start);
    printf("Result = %.2f\\n", C[0][0]);
    return 0;
}}
'''

PAR_TEMPLATE = '''#include <stdio.h>
#include <omp.h>
#define N {N}
double A[N][N], B[N][N], C[N][N];
void initialize() {{
    int i, j;
    for (i = 0; i < N; i++)
        for (j = 0; j < N; j++) {{
            A[i][j] = (double)(i + j + 1);
            B[i][j] = (double)(i * j + 1);
            C[i][j] = 0.0;
        }}
}}
void matmul() {{
    int i, j, k;
    #pragma omp parallel for schedule(static) private(i,j,k) shared(A,B,C)
    for (i = 0; i < N; i++)
        for (j = 0; j < N; j++)
            for (k = 0; k < N; k++)
                C[i][j] += A[i][k] * B[k][j];
}}
int main() {{
    initialize();
    double start = omp_get_wtime();
    matmul();
    printf("Time: %.4f seconds\\n", omp_get_wtime() - start);
    printf("Result = %.2f\\n", C[0][0]);
    return 0;
}}
'''

SIZES   = [128, 256, 512]
THREADS = [1, 2, 4, 8]
RUNS    = 3


def run_benchmark():
    dir_path = os.path.dirname(os.path.abspath(sys.argv[1])) if len(sys.argv) > 1 else os.getcwd()
    seq_tmp  = os.path.join(dir_path, "_bm_seq.c")
    par_tmp  = os.path.join(dir_path, "_bm_par.c")
    seq_bin  = os.path.join(dir_path, "_bm_seq")
    par_bin  = os.path.join(dir_path, "_bm_par")

    print()
    print("=" * 65)
    print("  BENCHMARK — Sequential vs Parallel  (matmul only, init excluded)")
    print("  CSCI465/ECEN433 — Nile University")
    print("=" * 65)

    all_rows = []

    for size in SIZES:
        # Write fresh .c files with N baked in — no macro conflict possible
        with open(seq_tmp, 'w') as f: f.write(SEQ_TEMPLATE.format(N=size))
        with open(par_tmp, 'w') as f: f.write(PAR_TEMPLATE.format(N=size))

        if not compile_c(seq_tmp, seq_bin) or not compile_c(par_tmp, par_bin):
            print(f"  N={size}: compile error — skipping")
            continue

        seq_t, seq_val = run_binary(seq_bin, threads=1, runs=RUNS)
        if seq_t is None:
            print(f"  N={size}: run error — skipping")
            continue

        seq_out = f"{seq_val:.2f}" if seq_val is not None else "N/A"
        print(f"\n  N={size}  seq={seq_t:.4f}s  output={seq_out}")
        print(f"  {'Threads':>7}  {'Par(s)':>8}  {'Speedup':>8}  {'Eff%':>6}  {'Par output':>12}  Accuracy")
        print("  " + "-" * 60)

        for t in THREADS:
            par_t, par_val = run_binary(par_bin, threads=t, runs=RUNS)
            if par_t is None:
                print(f"  {t:>7}  TIMEOUT/ERROR")
                continue
            speedup    = round(seq_t / par_t, 3)
            efficiency = round((speedup / t) * 100, 1)
            match, acc = check_accuracy(seq_val, par_val)
            par_out    = f"{par_val:.2f}" if par_val is not None else "N/A"
            print(f"  {t:>7}  {par_t:>8.4f}s  {speedup:>8.2f}x  {efficiency:>5.1f}%  {par_out:>12}  {acc}")
            all_rows.append({
                "size": size, "threads": t,
                "seq_time": seq_t, "par_time": par_t,
                "speedup": speedup, "efficiency": efficiency,
                "seq_val": seq_val, "par_val": par_val, "accuracy_match": match
            })

    cleanup(seq_tmp, par_tmp, seq_bin, par_bin)

    if all_rows:
        out_json = os.path.join(dir_path, "benchmark_results.json")
        with open(out_json, 'w') as f:
            json.dump(all_rows, f, indent=2)
        best = max(all_rows, key=lambda r: r['speedup'])
        print(f"\n  Best: {best['speedup']:.2f}x ({best['threads']} threads, N={best['size']})"
              f"  |  saved: benchmark_results.json")
    print("=" * 65)


# ==============================================================================
# ENTRY POINT
# ==============================================================================

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage:   python3 auto_parallelizer.py <input.c> <output.c> [--speedup | --benchmark]")
        print("Example: python3 auto_parallelizer.py matmul_seq.c matmul_par.c --speedup")
        print("         python3 auto_parallelizer.py matmul_seq.c matmul_par.c --benchmark")
        sys.exit(1)

    input_file  = sys.argv[1]
    output_file = sys.argv[2]

    if not os.path.exists(input_file):
        print(f"Error: '{input_file}' not found"); sys.exit(1)
    if os.path.getsize(input_file) == 0:
        print(f"Error: '{input_file}' is empty"); sys.exit(1)
    if not input_file.endswith('.c'):
        print(f"Warning: '{input_file}' has no .c extension — continuing")
    if not output_file.endswith('.c'):
        output_file += '.c'
        print(f"Note: output renamed to '{output_file}'")

    auto_parallelize(input_file, output_file)

    if "--benchmark" in sys.argv:
        run_benchmark()
    else:
        measure_speedup(input_file, output_file, thread_counts=[1, 2, 4, 8])