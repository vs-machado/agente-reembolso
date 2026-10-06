const mensagens = document.querySelector("#mensagens");
const boasVindas = document.querySelector("#boas-vindas");
const iniciar = document.querySelector("#iniciar");
const controles = document.querySelector("#controles");
const caso = document.querySelector("#caso");
const estado = document.querySelector("#estado");
const erro = document.querySelector("#erro");

function adicionarMensagem(autor, texto, nomeArquivo) {
  const elemento = document.createElement("article");
  elemento.className = `message ${autor}`;
  const titulo = document.createElement("span");
  titulo.className = "speaker";
  titulo.textContent = autor === "user" ? "Beneficiário" : "Agente de Reembolso";
  const balao = document.createElement("div");
  balao.className = "bubble";
  balao.textContent = texto;
  if (nomeArquivo) {
    const anexo = document.createElement("span");
    anexo.className = "file-name";
    anexo.textContent = `Anexo: ${nomeArquivo}`;
    balao.append(anexo);
  }
  elemento.append(titulo, balao);
  mensagens.append(elemento);
  mensagens.scrollTop = mensagens.scrollHeight;
  return elemento;
}

function adicionarAviso(texto) {
  const aviso = document.createElement("div");
  aviso.className = "notice";
  aviso.textContent = texto;
  mensagens.append(aviso);
  mensagens.scrollTop = mensagens.scrollHeight;
}

function formatarValor(valor) {
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(Number(valor));
}

function adicionarDetalhes(elemento, dados) {
  const linhas = [
    ["Decisão", dados.decisao],
    ["Reembolso", dados.valor_reembolso_brl == null ? null : formatarValor(dados.valor_reembolso_brl)],
    ["Protocolo", dados.protocolo],
  ].filter(([, valor]) => valor);
  if (!linhas.length) return;
  const detalhes = document.createElement("div");
  detalhes.className = "details";
  for (const [rotulo, valor] of linhas) {
    const linha = document.createElement("p");
    const destaque = document.createElement("strong");
    destaque.textContent = `${rotulo}: `;
    linha.append(destaque, document.createTextNode(valor));
    detalhes.append(linha);
  }
  elemento.append(detalhes);
}

function mostrarEvento(evento) {
  if (evento.tipo === "caso") {
    adicionarAviso(evento.titulo);
  } else if (evento.tipo === "turno") {
    adicionarMensagem("user", evento.mensagem || "(Enviou apenas um anexo)", evento.anexo);
    const resposta = adicionarMensagem("bot", evento.erro ? `Falha neste turno: ${evento.erro}` : evento.resposta);
    adicionarDetalhes(resposta, evento.estruturado || {});
  } else if (evento.tipo === "avaliacao") {
    adicionarAviso(`Avaliação de ${evento.conversa}: ${evento.nota}/100 · ${evento.turnos_atendidos} turnos atendidos${evento.motivo ? ` · ${evento.motivo}` : ""}`);
    for (const divergencia of evento.divergencias_do_desfecho) adicionarAviso(`Divergência: ${divergencia}`);
  } else if (evento.tipo === "avaliacao_indisponivel") {
    adicionarAviso(`A avaliação de ${evento.conversa} está indisponível. A conversa foi exibida e o treino continuará.`);
  } else if (evento.tipo === "fim") {
    if (evento.sem_avaliacao) {
      const parcial = evento.nota == null
        ? "Sem nota final."
        : `Nota parcial das ${evento.conversas} avaliada(s): ${evento.nota}/100.`;
      adicionarAviso(`Treino concluído. ${evento.sem_avaliacao} conversa(s) sem avaliação. ${parcial}`);
    } else {
      adicionarAviso(`Treino concluído. Nota final: ${evento.nota}/100 (${evento.conversas} conversa(s)).`);
    }
    estado.textContent = "Treino concluído. Atualize a página para iniciar outra simulação.";
  } else if (evento.tipo === "erro") {
    throw new Error(evento.mensagem);
  }
}

iniciar.addEventListener("click", async () => {
  iniciar.disabled = true;
  caso.disabled = true;
  controles.hidden = true;
  erro.hidden = true;
  mensagens.replaceChildren();
  estado.textContent = "Simulação em andamento. Aguarde os turnos e a avaliação.";
  let concluiu = false;
  try {
    const resposta = await fetch(`/treino?caso=${encodeURIComponent(caso.value)}`);
    if (!resposta.ok || !resposta.body) throw new Error(`Não foi possível iniciar o treino (HTTP ${resposta.status}).`);
    const leitor = resposta.body.getReader();
    const decodificador = new TextDecoder();
    let restante = "";
    while (true) {
      const { value, done } = await leitor.read();
      restante += decodificador.decode(value || new Uint8Array(), { stream: !done });
      const linhas = restante.split("\n");
      restante = linhas.pop();
      for (const linha of linhas) {
        if (!linha) continue;
        const evento = JSON.parse(linha);
        mostrarEvento(evento);
        if (evento.tipo === "fim") concluiu = true;
      }
      if (done) break;
    }
    if (!concluiu) throw new Error("A conexão foi encerrada antes de concluir o treino.");
  } catch (falha) {
    controles.hidden = false;
    erro.textContent = falha.message || "A simulação foi interrompida.";
    erro.hidden = false;
    estado.textContent = "A simulação não foi concluída.";
  } finally {
    iniciar.disabled = false;
    caso.disabled = false;
  }
});
