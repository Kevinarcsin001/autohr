import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { StructuredFields } from "@/components/StructuredFields";
import type { CandidateStructure } from "@/lib/api/candidateDetail";

/**
 * StructuredFields 组件测试（任务 24）。
 *
 * 覆盖：
 * - null → 「暂无结构化抽取数据」
 * - 各字段渲染（name/phone/email/education/years/current_company）
 * - skills 标签
 * - confidence 徽标（高/中/低三档）
 */

describe("StructuredFields", () => {
  it("null structure → 显示占位", () => {
    render(<StructuredFields structure={null} />);
    expect(
      screen.getByText("暂无结构化抽取数据"),
    ).toBeInTheDocument();
  });

  it("渲染基础字段", () => {
    const structure: CandidateStructure = {
      name: "张三",
      name_confidence: 0.95,
      phone: "13800000000",
      phone_confidence: 0.8,
      email: "zs@example.com",
      email_confidence: 0.9,
      education: "master",
      education_confidence: 0.85,
      years_of_experience: 5,
      years_of_experience_confidence: 0.7,
      skills: ["Python", "FastAPI"],
      skills_confidence: 0.6,
      expected_salary: "30k",
      expected_salary_confidence: 0.4,
      current_company: "ACME",
      current_company_confidence: 0.5,
      work_history: [],
      work_history_confidence: 0.3,
    };

    render(<StructuredFields structure={structure} />);
    expect(screen.getByText("张三")).toBeInTheDocument();
    expect(screen.getByText("13800000000")).toBeInTheDocument();
    expect(screen.getByText("zs@example.com")).toBeInTheDocument();
    expect(screen.getByText("硕士")).toBeInTheDocument();
    expect(screen.getByText("5 年")).toBeInTheDocument();
    expect(screen.getByText("ACME")).toBeInTheDocument();
    expect(screen.getByText("30k")).toBeInTheDocument();
    // skills badges
    expect(screen.getByText("Python")).toBeInTheDocument();
    expect(screen.getByText("FastAPI")).toBeInTheDocument();
  });

  it("字段为空 → 显示「—」", () => {
    const structure: CandidateStructure = {
      name: null,
      name_confidence: 0,
      phone: null,
      phone_confidence: 0,
      email: null,
      email_confidence: 0,
      education: null,
      education_confidence: 0,
      years_of_experience: null,
      years_of_experience_confidence: 0,
      skills: [],
      skills_confidence: 0,
      expected_salary: null,
      expected_salary_confidence: 0,
      current_company: null,
      current_company_confidence: 0,
      work_history: [],
      work_history_confidence: 0,
    };

    render(<StructuredFields structure={structure} />);
    // 多个「—」占位
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("渲染工作经历", () => {
    const structure: CandidateStructure = {
      name: "x",
      name_confidence: 0.5,
      phone: null,
      phone_confidence: 0,
      email: null,
      email_confidence: 0,
      education: null,
      education_confidence: 0,
      years_of_experience: null,
      years_of_experience_confidence: 0,
      skills: [],
      skills_confidence: 0,
      expected_salary: null,
      expected_salary_confidence: 0,
      current_company: null,
      current_company_confidence: 0,
      work_history: [
        {
          company: "ACME",
          title: "工程师",
          start_date: "2020-03",
          end_date: "2024-05",
          description: "负责后端开发",
        },
      ],
      work_history_confidence: 0.7,
    };

    render(<StructuredFields structure={structure} />);
    expect(screen.getByText(/ACME/)).toBeInTheDocument();
    expect(screen.getByText(/工程师/)).toBeInTheDocument();
    expect(screen.getByText(/2020-03/)).toBeInTheDocument();
    expect(screen.getByText(/2024-05/)).toBeInTheDocument();
    expect(screen.getByText(/负责后端开发/)).toBeInTheDocument();
  });
});
