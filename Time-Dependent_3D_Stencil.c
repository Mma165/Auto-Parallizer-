#include <stdio.h>
#include <omp.h>

#ifndef N
#define N 128
#endif

#ifndef T
#define T 20
#endif

double A[N][N][N];
double B[N][N][N];

int main()
{
    int t, i, j, k;

    double start, end;

    for(i = 0; i < N; i++)
    {
        for(j = 0; j < N; j++)
        {
            for(k = 0; k < N; k++)
            {
                A[i][j][k] = (i + j + k) * 0.1;
                B[i][j][k] = 0;
            }
        }
    }

    start = omp_get_wtime();

    for(t = 0; t < T; t++)
    {
        for(i = 1; i < N - 1; i++)
        {
            for(j = 1; j < N - 1; j++)
            {
                for(k = 1; k < N - 1; k++)
                {
                    B[i][j][k] =
                          A[i][j][k]
                        + A[i-1][j][k]
                        + A[i+1][j][k]
                        + A[i][j-1][k]
                        + A[i][j+1][k]
                        + A[i][j][k-1]
                        + A[i][j][k+1];
                }
            }
        }

        for(i = 1; i < N - 1; i++)
        {
            for(j = 1; j < N - 1; j++)
            {
                for(k = 1; k < N - 1; k++)
                {
                    A[i][j][k] = B[i][j][k];
                }
            }
        }
    }

    end = omp_get_wtime();

    printf("Time: %.6f\n", end - start);
    printf("Sample: %f\n", A[50][50][50]);

    return 0;
}