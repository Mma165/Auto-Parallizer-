#include <stdio.h>
#include <omp.h>

#ifndef N
#define N 512
#endif

#define TILE 32

double A[N][N];
double B[N][N];
double C[N][N];

int min(int a, int b)
{
    return (a < b) ? a : b;
}

int main()
{
    int ii, jj, kk;
    int i, j, k;

    double start, end;

    for(i = 0; i < N; i++)
    {
        for(j = 0; j < N; j++)
        {
            A[i][j] = i + j;
            B[i][j] = i - j;
            C[i][j] = 0;
        }
    }

    start = omp_get_wtime();

    for(ii = 0; ii < N; ii += TILE)
    {
        for(jj = 0; jj < N; jj += TILE)
        {
            for(kk = 0; kk < N; kk += TILE)
            {
                for(i = ii; i < min(ii + TILE, N); i++)
                {
                    for(j = jj; j < min(jj + TILE, N); j++)
                    {
                        double sum = C[i][j];

                        for(k = kk; k < min(kk + TILE, N); k++)
                        {
                            sum += A[i][k] * B[k][j];
                        }

                        C[i][j] = sum;
                    }
                }
            }
        }
    }

    end = omp_get_wtime();

    printf("Time: %.6f\n", end - start);
    printf("Sample: %f\n", C[100][100]);

    return 0;
}