/*
 * ============================================================
 * TEST 6: Prefix Sum — UNSAFE — expects SKIPPED
 * Dependency: A[i] = A[i-1] + 1 (loop-carried)
 * ============================================================
 */
#include <stdio.h>
#include <omp.h>
#define N 100000

double A[N];

void prefix_sum() {
    int i;
    A[0] = 1.0;
    for (i = 1; i < N; i++) {
        A[i] = A[i-1] + 1.0;
    }
}

int main() {
    double start, elapsed;

    start = omp_get_wtime();
    prefix_sum();
    elapsed = omp_get_wtime() - start;

    printf("Prefix Sum (N=%d)\n", N);
    printf("Time: %.4f seconds\n", elapsed);
    printf("A[0]=%.1f A[N-1]=%.1f\n", A[0], A[N-1]);
    return 0;
}
