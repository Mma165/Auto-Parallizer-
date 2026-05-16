#include <stdio.h>
#include <omp.h>

#ifndef H
#define H 512
#endif

#ifndef W
#define W 512
#endif

#ifndef C
#define C 3
#endif

float input[H][W][C];
float output[H][W][C];
float kernel[7][7];

int main()
{
    int i, j, c, ki, kj;

    double start, end;

    for(i = 0; i < H; i++)
    {
        for(j = 0; j < W; j++)
        {
            for(c = 0; c < C; c++)
            {
                input[i][j][c] =
                    (float)((i + j + c) % 255);

                output[i][j][c] = 0;
            }
        }
    }

    for(i = 0; i < 7; i++)
    {
        for(j = 0; j < 7; j++)
        {
            kernel[i][j] = 1.0f / 49.0f;
        }
    }

    start = omp_get_wtime();

    for(i = 3; i < H - 3; i++)
    {
        for(j = 3; j < W - 3; j++)
        {
            for(c = 0; c < C; c++)
            {
                float sum = 0;

                for(ki = -3; ki <= 3; ki++)
                {
                    for(kj = -3; kj <= 3; kj++)
                    {
                        sum +=
                            kernel[ki + 3][kj + 3]
                            * input[i + ki][j + kj][c];
                    }
                }

                output[i][j][c] = sum;
            }
        }
    }

    end = omp_get_wtime();

    printf("Time: %.6f\n", end - start);
    printf("Sample: %f\n", output[100][100][1]);

    return 0;
}