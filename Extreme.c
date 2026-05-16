#include <stdio.h>
#include <omp.h>

#ifndef N
#define N 256
#endif

#ifndef T
#define T 30
#endif

#define TILE 32

double A[N][N];
double B[N][N];
double C[N][N];

double X[N][N];
double Y[N][N];

double stencil_in[N][N][N];
double stencil_out[N][N][N];

int min(int a, int b)
{
    return (a < b) ? a : b;
}

int main()
{
    int t;
    int ii, jj, kk;
    int i, j, k;

    double start, end;

    // ------------------------------------------------------------
    // Initialization
    // ------------------------------------------------------------

    for(i = 0; i < N; i++)
    {
        for(j = 0; j < N; j++)
        {
            A[i][j] = i + j;
            B[i][j] = i - j;
            C[i][j] = 0;

            X[i][j] = 1.0;
            Y[i][j] = 0.0;
        }
    }

    for(i = 0; i < N; i++)
    {
        for(j = 0; j < N; j++)
        {
            for(k = 0; k < N; k++)
            {
                stencil_in[i][j][k] =
                    (i + j + k) * 0.01;

                stencil_out[i][j][k] = 0;
            }
        }
    }

    start = omp_get_wtime();

    // ============================================================
    // PHASE 1 — TILED MATRIX MULTIPLICATION
    // ============================================================

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

    // ============================================================
    // PHASE 2 — JACOBI-LIKE STENCIL ITERATIONS
    // ============================================================

    for(t = 0; t < T; t++)
    {
        for(i = 1; i < N - 1; i++)
        {
            for(j = 1; j < N - 1; j++)
            {
                for(k = 1; k < N - 1; k++)
                {
                    stencil_out[i][j][k] =
                          stencil_in[i][j][k]
                        + stencil_in[i-1][j][k]
                        + stencil_in[i+1][j][k]
                        + stencil_in[i][j-1][k]
                        + stencil_in[i][j+1][k]
                        + stencil_in[i][j][k-1]
                        + stencil_in[i][j][k+1];
                }
            }
        }

        for(i = 1; i < N - 1; i++)
        {
            for(j = 1; j < N - 1; j++)
            {
                for(k = 1; k < N - 1; k++)
                {
                    stencil_in[i][j][k] =
                        stencil_out[i][j][k];
                }
            }
        }
    }

    // ============================================================
    // PHASE 3 — 2D CONVOLUTION
    // ============================================================

    for(i = 1; i < N - 1; i++)
    {
        for(j = 1; j < N - 1; j++)
        {
            double sum = 0;

            for(ii = -1; ii <= 1; ii++)
            {
                for(jj = -1; jj <= 1; jj++)
                {
                    sum +=
                        A[i + ii][j + jj];
                }
            }

            Y[i][j] = sum / 9.0;
        }
    }

    // ============================================================
    // PHASE 4 — FIR-LIKE FILTER
    // ============================================================

    for(i = 16; i < N - 16; i++)
    {
        double sum = 0;

        for(k = -16; k <= 16; k++)
        {
            sum += X[i][i + k];
        }

        X[i][i] = sum;
    }

    end = omp_get_wtime();

    printf("Time: %.6f\n", end - start);

    printf("C[50][50] = %f\n", C[50][50]);
    printf("Stencil = %f\n", stencil_in[20][20][20]);
    printf("Y[40][40] = %f\n", Y[40][40]);
    printf("X[60][60] = %f\n", X[60][60]);

    return 0;
}