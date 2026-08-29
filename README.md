# Pronóstico de demanda de energía

Repositorio de trabajo colaborativo para el notebook de evaluación progresiva de modelos del Módulo 2.

## Contenido

- `notebooks/Modelo_pronostico_demanda_Modulo2_v3_guiado.ipynb`: comparación de baselines, regresión lineal, Ridge, árbol de decisión, Random Forest y Extra Trees.
- `src/preparar_datos_modelo_v3.py`: limpieza, homologación, deduplicación, calendarización y consolidación de las fuentes.
- `data/processed/`: bases procesadas y auditadas necesarias para ejecutar directamente el notebook.
- `requirements.txt`: dependencias para Google Colab o un entorno local.

## Datos

Los datos crudos no se almacenan en este repositorio. Las bases procesadas incluidas están agregadas y no contienen NIU, direcciones, coordenadas ni identificadores de facturas o medidores.

La carpeta `data/processed` contiene:

- `base_modelo_diaria.csv`
- `comparacion_mensual.csv`
- `consumo_calendarizado_segmento.csv`
- `indicadores_facturacion_mensual.csv`
- `diccionario_variables_relevantes.csv`
- `auditoria_esquemas.csv`
- `reporte_calidad.json`
- `manifest.json`

El notebook busca primero estos archivos dentro del repositorio. Los resultados de nuevas ejecuciones se escriben en `outputs/`, que permanece excluida de Git.

## Ejecución directa

1. Clonar el repositorio.
2. Entrar a su carpeta raíz.
3. Instalar las dependencias con `pip install -r requirements.txt`.
4. Abrir y ejecutar `notebooks/Modelo_pronostico_demanda_Modulo2_v3_guiado.ipynb`.

En Google Colab, el repositorio puede clonarse en `/content/pronostico-demanda-energia`. El notebook detecta esa ruta, la raíz actual o una ruta indicada mediante `MODULO2_PROJECT_DIR`.

## Regeneración de los datos

Solo es necesario ejecutar `src/preparar_datos_modelo_v3.py` cuando cambien las fuentes. En ese caso, los datos crudos deben estar disponibles externamente en:

En Google Drive se espera la siguiente ubicación:

`/content/drive/MyDrive/Proyecto de Grado/Datos`

El script también permite definir otra ubicación mediante la variable de entorno `PROYECTO_GRADO_DIR`.

La demanda real y la evaluación fuera de muestra permanecen limitadas a 2025. TC1 y TC2 de enero de 2026 se utilizan únicamente para cerrar los ciclos de facturación que contienen días de 2025.

## Trabajo colaborativo

Cada cambio debe realizarse en una rama separada y revisarse antes de integrarlo a `main`. No deben subirse fuentes crudas, credenciales, archivos de entorno ni resultados generados localmente.
