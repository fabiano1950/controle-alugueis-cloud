import streamlit as st
import pandas as pd
import os
import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaInMemoryUpload
from io import BytesIO
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# Configuração do Google Drive com suporte a local e Cloud
SCOPES = ['https://www.googleapis.com/auth/drive']
CREDS_FILE = 'service-account-credentials.json'

# Prioriza o arquivo local, usa st.secrets apenas no Cloud
if os.path.exists(CREDS_FILE):
    with open(CREDS_FILE, "r") as f:
        creds_dict = json.load(f)
elif hasattr(st, 'secrets') and "GOOGLE_CREDENTIALS" in st.secrets:
    creds_dict = st.secrets["GOOGLE_CREDENTIALS"]
    if isinstance(creds_dict, str):
        creds_dict = json.loads(creds_dict)
else:
    raise FileNotFoundError(f"O arquivo {CREDS_FILE} não foi encontrado localmente, e nenhum segredo GOOGLE_CREDENTIALS foi configurado no Streamlit Cloud.")

creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
drive_service = build('drive', 'v3', credentials=creds)

# IDs dos arquivos no Google Drive
DATA_FILE_ID = '1E7gNn-XNmZ2dux3ubJA2mkttvfsNZQnp'  # ID do financas_alugueis.csv
VACANCY_FILE_ID = '1aR6cBeBdoV0BjSJo3QjyvaK3uJ881jW2'  # ID do vacancia_alugueis.csv

# Listas de categorias
RECEITA_CATEGORIAS = ["Aluguel", "Outros"]
DESPESAS_CATEGORIAS = ["Internet", "Administração", "Luz", "Água", "IPTU", "Manutenção", "Outros"]

# Função para carregar dados financeiros do Google Drive
def load_data():
    request = drive_service.files().get_media(fileId=DATA_FILE_ID)
    fh = BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.seek(0)
    return pd.read_csv(fh) if fh.getvalue() else pd.DataFrame(columns=["Data", "Apartamento", "Descrição", "Tipo", "Categoria", "Valor"])

# Função para carregar dados de vacância do Google Drive
def load_vacancy():
    request = drive_service.files().get_media(fileId=VACANCY_FILE_ID)
    fh = BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.seek(0)
    if fh.getvalue():
        df = pd.read_csv(fh)
        if "Ocupado" not in df.columns:
            if "Status" in df.columns:
                df["Ocupado"] = df["Status"].str.lower() == "ocupado"
            else:
                raise KeyError("O arquivo vacancia_alugueis.csv não contém as colunas 'Ocupado' ou 'Status'. Verifique o CSV.")
        else:
            df["Ocupado"] = df["Ocupado"].astype(bool)
    else:
        df = pd.DataFrame({
            "Apartamento": [f"Apto {i}" for i in range(1, 17)],
            "Data": [datetime.now().date()] * 16,
            "Ocupado": [True] * 16
        })
    df["Ocupado"] = df["Ocupado"].astype(bool)
    return df[["Apartamento", "Ocupado", "Data"]]

# Função para salvar dados no Google Drive
def save_data(df, file_id):
    output = BytesIO()
    if "Ocupado" in df.columns:
        df["Status"] = df["Ocupado"].apply(lambda x: "Ocupado" if x else "Vago")
        df_to_save = df[["Data", "Apartamento", "Status"]]
    else:
        df_to_save = df
    df_to_save.to_csv(output, index=False, encoding='utf-8-sig')
    output.seek(0)
    media = MediaInMemoryUpload(output.getvalue(), mimetype='text/csv', resumable=True)
    drive_service.files().update(fileId=file_id, media_body=media).execute()

# Função para gerar CSV com subtotais e totais
def generate_summary_csv(df):
    summary = []
    for apto in ["Comum"] + [f"Apto {i}" for i in range(1, 17)]:
        apto_data = df[df["Apartamento"] == apto]
        if not apto_data.empty:
            for tipo in ["Receita", "Despesa"]:
                tipo_data = apto_data[apto_data["Tipo"] == tipo]
                for cat in tipo_data["Categoria"].unique():
                    subtotal = tipo_data[tipo_data["Categoria"] == cat]["Valor"].sum()
                    summary.append({"Apartamento": apto, "Tipo": tipo, "Categoria": cat, "Subtotal": subtotal})
    summary_df = pd.DataFrame(summary)
    total = df[df["Tipo"] == "Receita"]["Valor"].sum() - df[df["Tipo"] == "Despesa"]["Valor"].sum()
    total_row = pd.DataFrame([{"Apartamento": "Total Geral", "Tipo": "", "Categoria": "", "Subtotal": total}])
    return pd.concat([summary_df, total_row], ignore_index=True).to_csv(sep=",", index=False, encoding='utf-8-sig')

# Função para gerar Excel com todos os registros
def generate_full_records_excel(df):
    output = BytesIO()
    df.to_excel(output, index=False, engine='openpyxl')
    output.seek(0)
    return output

# Função para gerar PDF
def generate_pdf_report(df, vacancy_df, filtro_mes=None, filtro_ano=None, filtro_apto=None):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.setFont("Helvetica", 12)
    y = 750

    c.drawString(50, y, "Relatório de Aluguéis")
    y -= 30

    if filtro_mes and filtro_ano:
        c.drawString(50, y, f"Período: {filtro_mes}/{filtro_ano}")
        y -= 20
    if filtro_apto:
        c.drawString(50, y, f"Apartamento: {filtro_apto}")
        y -= 20

    total_receitas = df[df["Tipo"] == "Receita"]["Valor"].sum()
    total_despesas = df[df["Tipo"] == "Despesa"]["Valor"].sum()
    saldo = total_receitas - total_despesas
    c.drawString(50, y, f"Total Receitas: R$ {total_receitas:.2f}")
    y -= 20
    c.drawString(50, y, f"Total Despesas: R$ {total_despesas:.2f}")
    y -= 20
    c.drawString(50, y, f"Saldo: R$ {saldo:.2f}")
    y -= 30

    taxa_vacancia = (len(vacancy_df[vacancy_df["Ocupado"] == False]) / 16) * 100
    c.drawString(50, y, f"Taxa de Vacância: {taxa_vacancia:.2f}%")
    y -= 30

    c.drawString(50, y, "Resumo por Apartamento:")
    y -= 20
    for _, row in pd.DataFrame([{"Apartamento": apto, "Receitas": df[(df["Apartamento"] == apto) & (df["Tipo"] == "Receita")]["Valor"].sum(), "Despesas": df[(df["Apartamento"] == apto) & (df["Tipo"] == "Despesa")]["Valor"].sum(), "Saldo": df[(df["Apartamento"] == apto) & (df["Tipo"] == "Receita")]["Valor"].sum() - df[(df["Apartamento"] == apto) & (df["Tipo"] == "Despesa")]["Valor"].sum(), "Status": "Ocupado" if not vacancy_df[vacancy_df["Apartamento"] == apto].empty and vacancy_df[vacancy_df["Apartamento"] == apto]["Ocupado"].iloc[0] else "Vago"} for apto in [f"Apto {i}" for i in range(1, 17)]]).iterrows():
        c.drawString(50, y, f"{row['Apartamento']}: Receitas R${row['Receitas']:.2f}, Despesas R${row['Despesas']:.2f}, {row['Status']}")
        y -= 20
        if y < 50:
            c.showPage()
            y = 750

    c.save()
    buffer.seek(0)
    return buffer

# Interface do app
st.title("Controle de Aluguéis - 16 Apartamentos")

# Estilização dos botões
st.markdown(
    """
    <style>
    div.stButton > button {
        background-color: #2E7D32;
        color: white;
        border: none;
        padding: 5px 15px;
        border-radius: 5px;
    }
    div.stButton > button:hover {
        background-color: #1B5E20;
        color: white;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# Carregar dados
df = load_data()
vacancy_df = load_vacancy()

# Filtros
st.subheader("Filtros")
col1, col2, col3 = st.columns(3)
with col1:
    filtro_mes = st.selectbox("Mês", ["Todos"] + [f"{i:02d}" for i in range(1, 13)], index=0)
with col2:
    filtro_ano = st.selectbox("Ano", ["Todos"] + [str(i) for i in range(2020, 2026)], index=0)
with col3:
    filtro_apto = st.selectbox("Apartamento", ["Todos"] + [f"Apto {i}" for i in range(1, 17)] + ["Comum"], index=0)

# Aplicar filtros
df_filtrado = df.copy()
if filtro_mes != "Todos":
    df_filtrado = df_filtrado[pd.to_datetime(df_filtrado["Data"]).dt.month == int(filtro_mes)]
if filtro_ano != "Todos":
    df_filtrado = df_filtrado[pd.to_datetime(df_filtrado["Data"]).dt.year == int(filtro_ano)]
if filtro_apto != "Todos":
    df_filtrado = df_filtrado[df_filtrado["Apartamento"] == filtro_apto]

# Inicializar estado
if "form_state" not in st.session_state:
    st.session_state.form_state = {"tipo": "Receita", "categoria": "Aluguel"}

# Seleção de Tipo fora do formulário
tipo = st.selectbox("Tipo", ["Receita", "Despesa"], key="tipo_select", on_change=lambda: st.session_state.update({"form_state": {"tipo": st.session_state.tipo_select, "categoria": RECEITA_CATEGORIAS[0] if st.session_state.tipo_select == "Receita" else DESPESAS_CATEGORIAS[0]}}))

# Formulário para entrada de receitas/despesas
st.subheader("Registrar Receita ou Despesa")
with st.form("entrada_form"):
    data = st.date_input("Data")
    apartamento = st.selectbox("Apartamento", ["Comum"] + [f"Apto {i}" for i in range(1, 17)])
    descricao = st.text_input("Descrição (ex.: Aluguel, Conta de Luz)")
    categoria_options = RECEITA_CATEGORIAS if st.session_state.form_state["tipo"] == "Receita" else DESPESAS_CATEGORIAS
    categoria_index = categoria_options.index(st.session_state.form_state["categoria"]) if st.session_state.form_state["categoria"] in categoria_options else 0
    categoria = st.selectbox("Categoria", categoria_options, index=categoria_index, key="categoria_select")
    valor = st.number_input("Valor (R$)", min_value=0.0, format="%.2f")
    submit = st.form_submit_button("Adicionar")

    if submit:
        new_entry = pd.DataFrame({
            "Data": [data],
            "Apartamento": [apartamento],
            "Descrição": [descricao],
            "Tipo": [st.session_state.form_state["tipo"]],
            "Categoria": [categoria],
            "Valor": [valor]
        })
        df = pd.concat([df, new_entry], ignore_index=True)
        save_data(df, DATA_FILE_ID)
        st.session_state.form_state["categoria"] = categoria
        st.success("Registro adicionado com sucesso!")
        st.rerun()

# Formulário para gerenciar vacância
st.subheader("Gerenciar Vacância")
with st.form("vacancia_form", clear_on_submit=True):
    apartamento_vacancia = st.selectbox("Apartamento (Vacância)", [f"Apto {i}" for i in range(1, 17)])
    mask = vacancy_df["Apartamento"] == apartamento_vacancia
    if not vacancy_df.empty and mask.any():
        ocupado = st.checkbox("Ocupado?", value=vacancy_df[mask]["Ocupado"].iloc[0])
    else:
        ocupado = st.checkbox("Ocupado?", value=True)
    submit_vacancia = st.form_submit_button("Atualizar Vacância")

    if submit_vacancia:
        if apartamento_vacancia in vacancy_df["Apartamento"].values:
            vacancy_df.loc[vacancy_df["Apartamento"] == apartamento_vacancia, "Ocupado"] = ocupado
        else:
            new_row = pd.DataFrame({
                "Apartamento": [apartamento_vacancia],
                "Data": [datetime.now().date()],
                "Ocupado": [ocupado]
            })
            vacancy_df = pd.concat([vacancy_df, new_row], ignore_index=True)
        vacancy_df.loc[vacancy_df["Apartamento"] == apartamento_vacancia, "Data"] = datetime.now().date()
        save_data(vacancy_df, VACANCY_FILE_ID)
        vacancy_df = load_vacancy()
        st.success(f"Status de {apartamento_vacancia} atualizado!")
        st.rerun()

# Gerenciar lançamentos
st.subheader("Editar ou Excluir Lançamentos")
st.write("Clique em 'Atualizar Lista' após alterações para atualizar a tabela.")
if not df_filtrado.empty:
    edit_buttons = []
    delete_buttons = []
    for idx, row in df_filtrado.iterrows():
        col1, col2, col3 = st.columns([4, 1, 1])
        with col1:
            st.write(f"{idx}: {row['Descrição']} - {row['Tipo']} - R${row['Valor']:.2f}")
        with col2:
            if st.button("✏️", key=f"edit_{idx}"):
                edit_buttons.append(idx)
        with col3:
            if st.button("🗑️", key=f"delete_{idx}"):
                delete_buttons.append(idx)

    if delete_buttons:
        for idx in delete_buttons:
            global_idx = df.index[df_filtrado.index[idx]]
            df = df.drop(global_idx).reset_index(drop=True)
            save_data(df, DATA_FILE_ID)
            st.success(f"Lançamento {idx} excluído com sucesso!")
        st.rerun()

    if edit_buttons:
        for idx in edit_buttons:
            global_idx = df.index[df_filtrado.index[idx]]
            lancamento = df.loc[global_idx]
            with st.form(f"editar_form_{idx}", clear_on_submit=True):
                edit_data = st.date_input("Data", value=pd.to_datetime(lancamento["Data"]).date())
                edit_apartamento = st.selectbox("Apartamento", ["Comum"] + [f"Apto {i}" for i in range(1, 17)], index=([f"Apto {i}" for i in range(1, 17)] + ["Comum"]).index(lancamento["Apartamento"]))
                edit_descricao = st.text_input("Descrição", value=lancamento["Descrição"])
                edit_tipo = st.selectbox("Tipo", ["Receita", "Despesa"], index=["Receita", "Despesa"].index(lancamento["Tipo"]))
                edit_categoria_options = RECEITA_CATEGORIAS if edit_tipo == "Receita" else DESPESAS_CATEGORIAS
                edit_categoria_index = edit_categoria_options.index(lancamento["Categoria"]) if lancamento["Categoria"] in edit_categoria_options else 0
                edit_categoria = st.selectbox("Categoria", edit_categoria_options, index=edit_categoria_index, key=f"categoria_edit_{idx}")
                edit_valor = st.number_input("Valor (R$)", min_value=0.0, value=float(lancamento["Valor"]), format="%.2f")
                submit_edit = st.form_submit_button("Salvar Alterações")

                if submit_edit:
                    # Exclui a linha antiga
                    df = df.drop(global_idx).reset_index(drop=True)
                    # Adiciona a nova linha com os dados editados
                    new_entry = pd.DataFrame({
                        "Data": [edit_data.strftime('%Y-%m-%d')],
                        "Apartamento": [edit_apartamento],
                        "Descrição": [edit_descricao],
                        "Tipo": [edit_tipo],
                        "Categoria": [edit_categoria],
                        "Valor": [edit_valor]
                    })
                    df = pd.concat([df, new_entry], ignore_index=True)
                    # Salva as alterações
                    save_data(df, DATA_FILE_ID)
                    st.success(f"Lançamento {idx} atualizado com sucesso!")
                    st.rerun()

if st.button("Atualizar Lista"):
    st.rerun()

# Notificações
st.subheader("Notificações")
vagos_prolongados = vacancy_df[
    (vacancy_df["Ocupado"] == False) &
    ((datetime.now().date() - pd.to_datetime(vacancy_df["Data"]).dt.date) > timedelta(days=30))
]
if not vagos_prolongados.empty:
    st.warning("Apartamentos vagos há mais de 30 dias:")
    for _, row in vagos_prolongados.iterrows():
        dias_vago = (datetime.now().date() - pd.to_datetime(row["Data"]).date()).days
        st.write(f"- {row['Apartamento']}: Vago há {dias_vago} dias")

# Relatórios
st.subheader("Relatórios")
if not df_filtrado.empty:
    total_receitas = df_filtrado[df_filtrado["Tipo"] == "Receita"]["Valor"].sum()
    total_despesas = df_filtrado[df_filtrado["Tipo"] == "Despesa"]["Valor"].sum()
    saldo = total_receitas - total_despesas

    st.write(f"**Total de Receitas:** R$ {total_receitas:.2f}")
    st.write(f"**Total de Despesas:** R$ {total_despesas:.2f}")
    st.write(f"**Saldo:** R$ {saldo:.2f}")

    taxa_vacancia = (len(vacancy_df[vacancy_df["Ocupado"] == False]) / 16) * 100
    st.write(f"**Taxa de Vacância:** {taxa_vacancia:.2f}% ({len(vacancy_df[vacancy_df['Ocupado'] == False])} de 16 apartamentos vagos)")

    st.subheader("Resumo por Apartamento")
    resumo = [
        {
            "Apartamento": apto,
            "Receitas": df[(df["Apartamento"] == apto) & (df["Tipo"] == "Receita")]["Valor"].sum(),
            "Despesas": df[(df["Apartamento"] == apto) & (df["Tipo"] == "Despesa")]["Valor"].sum(),
            "Saldo": df[(df["Apartamento"] == apto) & (df["Tipo"] == "Receita")]["Valor"].sum() -
                     df[(df["Apartamento"] == apto) & (df["Tipo"] == "Despesa")]["Valor"].sum(),
            "Status": "Ocupado" if not vacancy_df[vacancy_df["Apartamento"] == apto].empty and vacancy_df[vacancy_df["Apartamento"] == apto]["Ocupado"].iloc[0] else "Vago"
        } for apto in [f"Apto {i}" for i in range(1, 17)]
    ]
    resumo_df = pd.DataFrame(resumo)
    def highlight_vacant(val):
        return 'background-color: #FFCCCC' if val == "Vago" and isinstance(val, str) else ''
    styled_df = resumo_df.style.map(highlight_vacant, subset=['Status'])
    st.dataframe(styled_df)

    st.subheader("Gráficos")
    st.write("Receitas vs. Despesas por Categoria")
    chart_data = df_filtrado.groupby(["Tipo", "Categoria"])["Valor"].sum().unstack().fillna(0)
    st.bar_chart(chart_data)

    st.write("Vacância por Apartamento")
    vacancy_chart = vacancy_df.groupby("Ocupado").size().rename({True: "Ocupado", False: "Vago"}).reindex(["Ocupado", "Vago"], fill_value=0)
    st.bar_chart(vacancy_chart)

    pdf_buffer = generate_pdf_report(df_filtrado, vacancy_df, filtro_mes, filtro_ano, filtro_apto)
    st.download_button(
        label="Baixar Relatório em PDF",
        data=pdf_buffer,
        file_name="relatorio_alugueis.pdf",
        mime="application/pdf"
    )

# Exibir todos os registros
st.subheader("Todos os Registros")
st.dataframe(df_filtrado)

# Downloads
if not df.empty:
    st.download_button(
        label="Baixar dados financeiros como CSV",
        data=df.to_csv(index=False, encoding='utf-8-sig'),
        file_name="financas_alugueis.csv",
        mime="text/csv"
    )
    st.download_button(
        label="Baixar todos os registros (Excel)",
        data=generate_full_records_excel(df),
        file_name="todos_registros_alugueis.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    st.download_button(
        label="Baixar dados de vacância como CSV",
        data=vacancy_df.to_csv(index=False, encoding='utf-8-sig'),
        file_name="vacancia_alugueis.csv",
        mime="text/csv"
    )
