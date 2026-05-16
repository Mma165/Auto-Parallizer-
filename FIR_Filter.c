#include <stdio.h>
#include <omp.h>

#ifndef N
#define N 1000000
#endif

#ifndef TAPS
#define TAPS 64
#endif

float x[N + TAPS];
float h1[TAPS];
float h2[TAPS];

float y1[N];
float y2[N];

int main()
{
    int n, k;

    double start, end;

    for(n = 0; n < N + TAPS; n++)
        x[n] = (float)(n % 100);

    for(k = 0; k < TAPS; k++)
    {
        h1[k] = 0.01f * k;
        h2[k] = 0.02f * k;
    }

    start = omp_get_wtime();

    for(n = 0; n < N; n++)
    {
        float sum1 = 0;
        float sum2 = 0;

        for(k = 0; k < TAPS; k++)
        {
            sum1 += h1[k] * x[n - k + TAPS];
            sum2 += h2[k] * x[n - k + TAPS];
        }

        y1[n] = sum1;
        y2[n] = sum2;
    }

    end = omp_get_wtime();

    printf("Time: %.6f\n", end - start);
    printf("Sample: %f\n", y1[100]);

    return 0;
}