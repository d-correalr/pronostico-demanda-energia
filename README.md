# Pronóstico de demanda de energía

Repositorio de trabajo colaborativo para el notebook de evaluación progresiva de modelos del Módulo 2.

## Contenido

- `notebooks/Modelo_pronostico_demanda_Modulo2_v3_guiado.ipynb`: comparación de baselines, regresión lineal, Ridge, árbol de decisión, Random Forest y Extra Trees.
- `src/preparar_datos_modelo_v3.py`: limpieza, homologación, deduplicación, calendarización y consolidación de las fuentes.
- `requirements.txt`: dependencias para Google Colab o un entorno local.

## Datos

Los datos crudos, las bases procesadas y las salidas de ejecución no se almacenan en este repositorio.

En Google Drive se espera la siguiente ubicación:

`/content/drive/MyDrive/Proyecto de Grado/Datos`

El script también permite definir otra ubicación mediante la variable de entorno `PROYECTO_GRADO_DIR`.

## Ejecución

1. Clonar el repositorio dentro de `/content/drive/MyDrive/Proyecto de Grado` o establecer `PROYECTO_GRADO_DIR`.
2. Instalar las dependencias con `pip install -r requirements.txt`.
3. Ejecutar `src/preparar_datos_modelo_v3.py` cuando cambien las fuentes.
4. Abrir y ejecutar el notebook de `notebooks`.

La demanda real y la evaluación fuera de muestra permanecen limitadas a 2025. TC1 y TC2 de enero de 2026 se utilizan únicamente para cerrar los ciclos de facturación que contienen días de 2025.

## Trabajo colaborativo

Cada cambio debe realizarse en una rama separada y revisarse antes de integrarlo a `main`. No deben subirse datos, credenciales, archivos de entorno ni resultados generados localmente.
